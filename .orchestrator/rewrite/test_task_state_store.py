from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_state_store as store


REPO_ROOT = Path(__file__).resolve().parents[2]
INCIDENT_TASK_COUNT = 22
INCIDENT_SEQUENCE = 1592


def state(status: str, *, next_value: str) -> dict:
    return {
        "tasks": [
            {
                "id": "T1",
                "status": status,
                "owner": "Codex",
                "reviewer": "Claude",
                "next": next_value,
            }
        ]
    }


def board(*rows: tuple[str, str], **extra) -> dict:
    payload = {"tasks": [{"id": task_id, "status": status} for task_id, status in rows]}
    payload.update(extra)
    return payload


def drain_marker(*task_ids: str, **extra) -> dict:
    marker = {
        "reason": "operator drained a stuck board",
        "actor": "Human/Ops",
        "approved_at": "2026-07-26T00:00:00Z",
        "task_ids": list(task_ids),
    }
    marker.update(extra)
    return marker


def test_append_replays_latest_state_and_retains_history(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    first = state("todo", next_value="first")
    second = state("in_progress", next_value="second")

    first_event = store.append_state_commit(path, first, source="test", committed_at="2026-07-20T07:00:00Z")
    second_event = store.append_state_commit(path, second, source="test", committed_at="2026-07-20T07:01:00Z")
    events = store.load_events(path)

    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["state"] == first
    assert events[1]["state"] == second
    assert second_event["previous_event_sha256"] == first_event["event_sha256"]
    assert store.project_latest_state(events) == second
    assert store.verify_projection(path, second)["ok"] is True


def test_identical_state_commit_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    payload = state("todo", next_value="same")

    first = store.append_state_commit(path, payload, source="test")
    second = store.append_state_commit(path, payload, source="test")

    assert second == first
    assert len(store.load_events(path)) == 1


def test_projection_of_prefix_is_point_in_time_state(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    first = state("todo", next_value="first")
    second = state("review", next_value="review it")
    store.append_state_commit(path, first, source="test")
    store.append_state_commit(path, second, source="test")

    events = store.load_events(path)

    assert store.project_latest_state(events[:1]) == first
    assert store.project_latest_state(events) == second


def test_tampered_state_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, state("todo", next_value="first"), source="test")
    event = json.loads(path.read_text(encoding="utf-8"))
    event["state"]["tasks"][0]["status"] = "done"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(store.TaskStateStoreError, match="state digest mismatch"):
        store.load_events(path)


def test_broken_hash_chain_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, state("todo", next_value="first"), source="test")
    store.append_state_commit(path, state("review", next_value="second"), source="test")
    lines = path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["previous_event_sha256"] = "0" * 64
    path.write_text(lines[0] + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    with pytest.raises(store.TaskStateStoreError, match="previous hash mismatch"):
        store.load_events(path)


def test_symlink_event_log_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.jsonl"
    real.write_text("", encoding="utf-8")
    link = tmp_path / "events.jsonl"
    link.symlink_to(real)

    with pytest.raises(store.TaskStateStoreError, match="regular file"):
        store.append_state_commit(link, state("todo", next_value="first"), source="test")


def test_empty_journal_does_not_claim_projection_parity(tmp_path: Path) -> None:
    report = store.verify_projection(
        tmp_path / "missing.jsonl",
        state("todo", next_value="first"),
    )

    assert report["ok"] is False
    assert report["event_count"] == 0


def test_reject_nonterminal_collapse_to_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    first = state("todo", next_value="first")

    store.append_state_commit(path, first, source="test")
    with pytest.raises(store.TaskStateStoreError, match="mass replacement") as excinfo:
        store.append_state_commit(path, {"tasks": []}, source="test")

    assert "T1" in str(excinfo.value)
    events = store.load_events(path)
    assert len(events) == 1
    assert events[0]["state"] == first


def test_allow_first_bootstrap_with_empty_or_no_tasks(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, {"tasks": []}, source="test")
    store.append_state_commit(path, {"sprint": "L12"}, source="test")

    events = store.load_events(path)
    assert [event["state"] for event in events] == [{"tasks": []}, {"sprint": "L12"}]


def test_allow_drain_when_all_previous_tasks_were_terminal(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    all_done = state("done", next_value="finished")

    store.append_state_commit(path, all_done, source="test")
    store.append_state_commit(path, {"tasks": []}, source="test")

    events = store.load_events(path)
    assert len(events) == 2
    assert events[1]["state"] == {"tasks": []}


def test_completing_and_archiving_the_last_task_stays_possible(tmp_path: Path) -> None:
    """The regression the count-only guard caused: a legal final completion."""

    path = tmp_path / "task-state-events.jsonl"
    live = board(("L12-ONLY-001", "in_progress"))
    completed = board(("L12-ONLY-001", "done"))

    store.append_state_commit(path, live, source="ai-status")
    # Completion is a status transition, not a removal: identity is retained.
    store.append_state_commit(path, completed, source="ai-status")
    # The archive sweep then removes a row that is already terminal.
    store.append_state_commit(path, board(), source="ai-status")

    assert [event["state"] for event in store.load_events(path)] == [
        live,
        completed,
        {"tasks": []},
    ]


def test_terminal_rows_may_be_archived_while_live_work_remains(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("KEEP-001", "in_progress"), ("GONE-001", "done"), ("GONE-002", "superseded"))
    after = board(("KEEP-001", "review"))

    store.append_state_commit(path, before, source="ai-status")
    store.append_state_commit(path, after, source="ai-status")

    assert store.project_latest_state(store.load_events(path)) == after


def test_partial_nonterminal_disappearance_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("KEEP-001", "in_progress"), ("DROP-001", "review"))

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match="disappearance") as excinfo:
        store.append_state_commit(path, board(("KEEP-001", "in_progress")), source="rogue")

    message = str(excinfo.value)
    assert "DROP-001" in message
    assert "KEEP-001" not in message
    assert len(store.load_events(path)) == 1


def test_mass_replacement_with_fresh_task_ids_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("OLD-001", "in_progress"), ("OLD-002", "todo"))

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match="mass replacement"):
        store.append_state_commit(path, board(("NEW-001", "todo")), source="fixture")

    assert store.project_latest_state(store.load_events(path)) == before


def test_unknown_status_counts_as_live_work(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("ODD-001", "waiting-on-human"))

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, board(), source="rogue")

    assert store.project_latest_state(store.load_events(path)) == before


@pytest.mark.parametrize(
    "malformed_rows",
    [
        pytest.param([{"status": "in_progress"}], id="row-without-id"),
        pytest.param([{"id": "   ", "status": "todo"}], id="row-with-blank-id"),
        pytest.param(["TASK-001"], id="row-that-is-not-an-object"),
        pytest.param([None], id="row-that-is-null"),
    ],
)
def test_malformed_task_rows_are_protected_as_live_work(
    tmp_path: Path,
    malformed_rows: list,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = {"tasks": malformed_rows}

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, {"tasks": []}, source="rogue")

    assert store.project_latest_state(store.load_events(path)) == before


def test_malformed_task_container_is_protected_as_live_work(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = {"tasks": {"L12-ONLY-001": {"status": "in_progress"}}}

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, {"tasks": []}, source="rogue")

    assert store.project_latest_state(store.load_events(path)) == before


def test_dropping_the_task_plane_entirely_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("L12-ONLY-001", "in_progress"))

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, {"sprint": "L12"}, source="rogue")

    assert store.project_latest_state(store.load_events(path)) == before


def test_audited_drain_marker_authorizes_real_task_removal(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("KEEP-001", "in_progress"), ("DROP-001", "blocked"))
    drained = board(
        ("KEEP-001", "in_progress"),
        **{store.DRAIN_MARKER_KEY: drain_marker("DROP-001")},
    )

    store.append_state_commit(path, before, source="ai-status")
    store.append_state_commit(path, drained, source="ai-status")

    assert store.project_latest_state(store.load_events(path)) == drained


@pytest.mark.parametrize(
    "approved_at",
    [
        pytest.param("2026-07-26T00:00:00Z", id="utc-zulu"),
        pytest.param("2026-07-26T00:00:00+00:00", id="utc-offset"),
        pytest.param("2026-07-26T09:00:00+09:00", id="non-utc-offset"),
        pytest.param("2026-07-26T00:00:00.123456Z", id="fractional-seconds"),
    ],
)
def test_drain_marker_accepts_past_timezone_aware_approvals(
    tmp_path: Path,
    approved_at: str,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("KEEP-001", "in_progress"), ("DROP-001", "blocked"))
    drained = board(
        ("KEEP-001", "in_progress"),
        **{store.DRAIN_MARKER_KEY: drain_marker("DROP-001", approved_at=approved_at)},
    )

    store.append_state_commit(path, before, source="ai-status")
    store.append_state_commit(path, drained, source="ai-status")

    assert store.project_latest_state(store.load_events(path)) == drained


def test_drain_marker_accepts_an_approval_stamped_at_commit_time(tmp_path: Path) -> None:
    """The not-future rule must not reject a marker approved moments ago."""

    path = tmp_path / "task-state-events.jsonl"
    before = board(("KEEP-001", "in_progress"), ("DROP-001", "blocked"))
    drained = board(
        ("KEEP-001", "in_progress"),
        **{store.DRAIN_MARKER_KEY: drain_marker("DROP-001", approved_at=store.utc_now())},
    )

    store.append_state_commit(path, before, source="ai-status")
    store.append_state_commit(path, drained, source="ai-status")

    assert store.project_latest_state(store.load_events(path)) == drained


def test_drain_marker_for_unidentified_rows_must_not_name_phantom_ids(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = {"tasks": [{"status": "in_progress"}]}

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(
        store.TaskStateStoreError,
        match="not live removals in this commit: \\['PHANTOM-001'\\]",
    ):
        store.append_state_commit(
            path,
            {
                "tasks": [],
                store.DRAIN_MARKER_KEY: drain_marker(
                    "PHANTOM-001",
                    allow_unidentified=True,
                ),
            },
            source="rogue",
        )

    assert store.project_latest_state(store.load_events(path)) == before


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        pytest.param("drained", "must be an object", id="not-an-object"),
        pytest.param(
            drain_marker("DROP-001", reason=""),
            "lacks audit fields",
            id="missing-reason",
        ),
        pytest.param(
            drain_marker("DROP-001", actor="  "),
            "lacks audit fields",
            id="missing-actor",
        ),
        pytest.param(
            {"reason": "r", "actor": "Human/Ops", "approved_at": "2026-07-26T00:00:00Z"},
            "must list the removed task ids",
            id="missing-task-ids",
        ),
        pytest.param(
            drain_marker("OTHER-001"),
            "does not cover removed tasks: \\['DROP-001'\\]",
            id="wrong-task-ids",
        ),
        pytest.param(
            drain_marker("DROP-001", "KEEP-001"),
            "still on the board: \\['KEEP-001'\\]",
            id="preauthorizes-live-task",
        ),
        pytest.param(
            drain_marker("DROP-001", "NEVER-EXISTED-001"),
            "not live removals in this commit: \\['NEVER-EXISTED-001'\\]",
            id="pads-with-an-id-that-was-never-live",
        ),
        pytest.param(
            drain_marker("DROP-001", "DROP-001"),
            "repeats task ids: \\['DROP-001'\\]",
            id="duplicate-task-ids",
        ),
        pytest.param(
            drain_marker("DROP-001", task_ids=["DROP-001", 7]),
            "must list the removed task ids",
            id="non-string-task-id",
        ),
        pytest.param(
            drain_marker("DROP-001", reason=7),
            "lacks audit fields \\['reason'\\]",
            id="non-string-reason",
        ),
        pytest.param(
            drain_marker("DROP-001", actor=True),
            "lacks audit fields \\['actor'\\]",
            id="non-string-actor",
        ),
        pytest.param(
            drain_marker("DROP-001", approved_at=1784073600),
            "lacks audit fields \\['approved_at'\\]",
            id="non-string-approved-at",
        ),
        pytest.param(
            drain_marker("DROP-001", approved_at="not-a-timestamp"),
            "approved_at must be a timezone-aware ISO 8601 timestamp",
            id="unparseable-approved-at",
        ),
        pytest.param(
            drain_marker("DROP-001", approved_at="2026-07-26T00:00:00"),
            "approved_at must be a timezone-aware ISO 8601 timestamp",
            id="naive-approved-at",
        ),
        pytest.param(
            drain_marker("DROP-001", approved_at="2026-07-26"),
            "approved_at must be a timezone-aware ISO 8601 timestamp",
            id="date-only-approved-at",
        ),
        pytest.param(
            drain_marker("DROP-001", approved_at="2099-01-01T00:00:00Z"),
            "approved_at is in the future",
            id="future-approved-at",
        ),
    ],
)
def test_drain_marker_must_be_explicit_and_audited(
    tmp_path: Path,
    marker: object,
    expected: str,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = board(("KEEP-001", "in_progress"), ("DROP-001", "blocked"))

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match=expected):
        store.append_state_commit(
            path,
            board(("KEEP-001", "in_progress"), **{store.DRAIN_MARKER_KEY: marker}),
            source="rogue",
        )

    assert store.project_latest_state(store.load_events(path)) == before


def test_drain_marker_cannot_be_carried_forward_to_cover_a_later_drop(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    marker = drain_marker("DROP-001")
    store.append_state_commit(
        path,
        board(("KEEP-001", "in_progress"), ("DROP-001", "blocked")),
        source="ai-status",
    )
    store.append_state_commit(
        path,
        board(("KEEP-001", "in_progress"), **{store.DRAIN_MARKER_KEY: marker}),
        source="ai-status",
    )

    # The same marker object, reused verbatim, must not license a second drop.
    with pytest.raises(store.TaskStateStoreError, match="unchanged copy of the previous commit"):
        store.append_state_commit(
            path,
            board(**{store.DRAIN_MARKER_KEY: marker}),
            source="rogue",
        )

    assert len(store.load_events(path)) == 2


def test_drain_marker_must_opt_in_to_removing_rows_without_ids(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    before = {"tasks": [{"status": "in_progress"}]}
    marker = drain_marker()

    store.append_state_commit(path, before, source="ai-status")
    with pytest.raises(store.TaskStateStoreError, match="must set allow_unidentified"):
        store.append_state_commit(
            path,
            {"tasks": [], store.DRAIN_MARKER_KEY: marker},
            source="rogue",
        )

    store.append_state_commit(
        path,
        {"tasks": [], store.DRAIN_MARKER_KEY: drain_marker(allow_unidentified=True)},
        source="ai-status",
    )
    assert store.project_latest_state(store.load_events(path))["tasks"] == []


def test_relative_event_log_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(store.TaskStateStoreError, match="must be an absolute path"):
        store.append_state_commit("relative_events.jsonl", {"tasks": []}, source="test")


def test_unrelated_workers_not_superseded_and_parity_preserved_after_rejected_drop(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    active_state = {
        "tasks": [
            {"id": "TASK-001", "status": "in_progress", "owner": "Codex", "reviewer": "Claude"},
            {"id": "TASK-002", "status": "todo", "owner": "Antigravity", "reviewer": "Claude"},
        ],
        "workers": {
            "worker-1": {"status": "running", "current_task_id": "TASK-001"},
            "worker-2": {"status": "idle"},
        },
    }

    store.append_state_commit(path, active_state, source="test")
    before_bytes = path.read_bytes()
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, {"tasks": []}, source="test")

    # A rejected write is byte invariant: nothing was appended or rewritten.
    assert path.read_bytes() == before_bytes

    events = store.load_events(path)
    latest = store.project_latest_state(events)
    assert len(events) == 1
    assert latest == active_state
    assert [task["status"] for task in latest["tasks"]] == ["in_progress", "todo"]
    assert latest["workers"]["worker-1"] == {"status": "running", "current_task_id": "TASK-001"}

    report = store.verify_projection(path, active_state)
    assert report["ok"] is True
    assert report["event_count"] == 1
    assert report["nonterminal_task_count"] == 2


def _seed_chain(path: Path, states: list[dict]) -> None:
    """Write a valid pre-existing journal without going through the guard."""

    lines: list[bytes] = []
    previous_sha256: str | None = None
    for sequence, payload in enumerate(states, start=1):
        event = {
            "version": store.EVENT_VERSION,
            "type": store.EVENT_TYPE_STATE_COMMITTED,
            "sequence": sequence,
            "committed_at": "2026-07-26T00:00:00Z",
            "source": "incident-replay-seed",
            "previous_event_sha256": previous_sha256,
            "state_sha256": store.sha256_json(payload),
            "state": payload,
        }
        event_sha256 = store.sha256_json(event)
        event["event_sha256"] = event_sha256
        event["event_id"] = f"task-state-{event_sha256}"
        previous_sha256 = event_sha256
        lines.append(store.canonical_json_bytes(event))
    path.write_bytes(b"\n".join(lines) + b"\n")


def _incident_board(revision: int) -> dict:
    return {
        "revision": revision,
        "tasks": [
            {
                "id": f"L12-TASK-{index:03d}",
                "status": "in_progress" if index % 2 else "todo",
                "owner": "Claude" if index % 3 else "Codex2",
            }
            for index in range(1, INCIDENT_TASK_COUNT + 1)
        ],
    }


def test_incident_replay_rejects_the_empty_snapshot_after_sequence_1592(
    tmp_path: Path,
) -> None:
    """Sequence 1592 held 22 live tasks; 1593 wrote an empty board and superseded them."""

    path = tmp_path / "task-state-events.jsonl"
    _seed_chain(path, [_incident_board(revision) for revision in range(1, INCIDENT_SEQUENCE + 1)])
    seeded = store.load_events(path)
    assert len(seeded) == INCIDENT_SEQUENCE
    assert len(seeded[-1]["state"]["tasks"]) == INCIDENT_TASK_COUNT
    before_bytes = path.read_bytes()

    with pytest.raises(store.TaskStateStoreError, match="mass replacement") as excinfo:
        store.append_state_commit(path, {"tasks": []}, source="worker-test-run")

    message = str(excinfo.value)
    assert f"remove {INCIDENT_TASK_COUNT} nonterminal task(s)" in message
    assert f"leaving 0 of {INCIDENT_TASK_COUNT} live tasks" in message
    # Rejected before append: the journal is byte identical and still sequence 1592.
    assert path.read_bytes() == before_bytes

    survived = store.load_events(path)
    assert len(survived) == INCIDENT_SEQUENCE
    assert survived[-1]["sequence"] == INCIDENT_SEQUENCE
    assert store.project_latest_state(survived) == seeded[-1]["state"]

    # Recovery: the board keeps advancing normally on top of the rejected write.
    recovery = _incident_board(INCIDENT_SEQUENCE + 1)
    recovery["tasks"][0]["status"] = "review"
    committed = store.append_state_commit(path, recovery, source="ai-status")

    assert committed["sequence"] == INCIDENT_SEQUENCE + 1
    assert committed["previous_event_sha256"] == survived[-1]["event_sha256"]
    report = store.verify_projection(path, recovery)
    assert report["ok"] is True
    assert report["event_count"] == INCIDENT_SEQUENCE + 1
    assert report["nonterminal_task_count"] == INCIDENT_TASK_COUNT


def test_guard_rejection_does_not_weaken_hash_chain_validation(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, board(("CHAIN-001", "in_progress")), source="test")
    with pytest.raises(store.TaskStateStoreError, match="nonterminal drop rejected"):
        store.append_state_commit(path, {"tasks": []}, source="rogue")

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["state"]["tasks"] = []
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    # Editing the journal to force the collapse still fails digest validation.
    with pytest.raises(store.TaskStateStoreError, match="state digest mismatch"):
        store.load_events(path)


def test_inherited_live_task_state_env_is_absent_during_tests() -> None:
    assert "PANTHEON_TASK_STATE_STORE_MODE" not in os.environ
    assert "PANTHEON_TASK_STATE_EVENT_LOG" not in os.environ


def _load_root_conftest():
    spec = importlib.util.spec_from_file_location(
        "pantheon_root_conftest",
        REPO_ROOT / "conftest.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_conftest_scrubs_an_inherited_live_event_log(monkeypatch) -> None:
    conftest = _load_root_conftest()
    monkeypatch.setenv("PANTHEON_TASK_STATE_STORE_MODE", "authoritative")
    monkeypatch.setenv("PANTHEON_TASK_STATE_EVENT_LOG", "/live/pantheon/task-state-events.jsonl")

    scrubbed = conftest.scrub_inherited_task_state_env()

    assert sorted(scrubbed) == [
        "PANTHEON_TASK_STATE_EVENT_LOG",
        "PANTHEON_TASK_STATE_STORE_MODE",
    ]
    assert "PANTHEON_TASK_STATE_STORE_MODE" not in os.environ
    assert "PANTHEON_TASK_STATE_EVENT_LOG" not in os.environ


def test_task_worktree_pytest_run_cannot_reach_an_inherited_live_event_log(
    tmp_path: Path,
) -> None:
    """A worker shell inherits the live journal binding; a pytest run must not use it."""

    if os.environ.get("PANTHEON_TASK_STATE_ISOLATION_CHILD") == "1":
        pytest.skip("inner isolation run must not recurse")
    live_event_log = tmp_path / "live" / "task-state-events.jsonl"
    environment = dict(os.environ)
    environment["PANTHEON_TASK_STATE_STORE_MODE"] = "authoritative"
    environment["PANTHEON_TASK_STATE_EVENT_LOG"] = str(live_event_log)
    environment["PANTHEON_TASK_STATE_ISOLATION_CHILD"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            f"{Path(__file__).relative_to(REPO_ROOT)}::test_inherited_live_task_state_env_is_absent_during_tests",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not live_event_log.exists()
    assert not live_event_log.parent.exists()


# ---------------------------------------------------------------------------
# Bounded-cost journal reads (SUP-TASK-STATE-LOCK-LATENCY-001)
#
# Live measurement being defended: a ~157MB / 2050-event journal made every
# lock-free Human/Ops note command take roughly 55-90s, because a single
# command replayed and revalidated the whole log four times -- load_events plus
# project_latest_state on the read, then load_events plus project_latest_state
# again inside append_state_commit's readback.
# ---------------------------------------------------------------------------


def growing_board(count: int) -> dict:
    """A board that only ever gains task ids, so the drop guard stays quiet."""

    return board(*[(f"T-{index}", "todo") for index in range(count)])


def _replays(monkeypatch) -> list[str]:
    """Record every full parse+validate pass over the journal."""

    seen: list[str] = []
    original = store._snapshot_from_payload

    def counting(payload: bytes, checkpoint):
        snapshot = original(payload, checkpoint)
        seen.append(
            "checkpoint" if snapshot["resumed_from_checkpoint"] else "full"
        )
        return snapshot

    monkeypatch.setattr(store, "_snapshot_from_payload", counting)
    return seen


def test_snapshot_reuses_the_checkpointed_prefix_and_only_parses_the_tail(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    for index in range(1, 6):
        store.append_state_commit(path, growing_board(index), source="test")

    warm = store.load_snapshot(path)

    assert warm["event_count"] == 5
    assert warm["resumed_from_checkpoint"] is True
    # The append already checkpointed the head, so a read that follows it has
    # no new events left to revalidate.
    assert warm["revalidated_events"] == 0

    store.append_state_commit(path, growing_board(6), source="test")
    incremental = store.load_snapshot(path)

    assert incremental["event_count"] == 6
    assert incremental["resumed_from_checkpoint"] is True
    assert incremental["revalidated_events"] == 0
    assert incremental["state"] == growing_board(6)


def test_checkpointed_snapshot_matches_a_forced_full_replay(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    for index in range(1, 7):
        store.append_state_commit(path, growing_board(index), source="test")

    accelerated = store.load_snapshot(path)
    monkeypatch.setenv(store.FULL_REPLAY_ENV, "1")
    audited = store.load_snapshot(path)

    assert audited["resumed_from_checkpoint"] is False
    assert audited["revalidated_events"] == 6
    for field in ("event_count", "last_event_id", "state", "state_sha256"):
        assert accelerated[field] == audited[field]
    assert store.project_latest_state(store.load_events(path)) == accelerated["state"]


def test_edited_history_is_rejected_even_though_a_checkpoint_exists(
    tmp_path: Path,
) -> None:
    """The checkpoint accelerates parsing; it never excuses a byte from hashing."""

    path = tmp_path / "task-state-events.jsonl"
    for index in range(1, 5):
        store.append_state_commit(path, growing_board(index), source="test")
    store.load_snapshot(path)

    lines = path.read_bytes().split(b"\n")
    tampered = json.loads(lines[1])
    tampered["source"] = "rogue-rewrite"
    lines[1] = json.dumps(
        tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.write_bytes(b"\n".join(lines))

    with pytest.raises(store.TaskStateStoreError):
        store.load_snapshot(path)


def test_truncated_journal_is_not_served_from_a_stale_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    for index in range(1, 5):
        store.append_state_commit(path, growing_board(index), source="test")
    store.load_snapshot(path)

    lines = [line for line in path.read_bytes().split(b"\n") if line]
    path.write_bytes(b"\n".join(lines[:2]) + b"\n")

    recovered = store.load_snapshot(path)

    assert recovered["event_count"] == 2
    assert recovered["resumed_from_checkpoint"] is False
    assert recovered["state"] == growing_board(2)


def test_unusable_checkpoint_degrades_to_full_replay(tmp_path: Path) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, growing_board(1), source="test")
    checkpoint = store._checkpoint_path(path)
    assert checkpoint.exists()
    checkpoint.write_bytes(b"not json at all")

    snapshot = store.load_snapshot(path)

    assert snapshot["resumed_from_checkpoint"] is False
    assert snapshot["event_count"] == 1
    # A healthy read repairs the cache instead of leaving it poisoned.
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["event_count"] == 1


def test_checkpoint_head_must_equal_the_actual_prefix_tail(tmp_path: Path) -> None:
    """A checkpoint-only write cannot substitute an internally valid state."""

    path = tmp_path / "task-state-events.jsonl"
    real_state = board(("REAL-001", "review"))
    store.append_state_commit(path, real_state, source="test")
    checkpoint_path = store._checkpoint_path(path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))

    forged_state = board(("FORGED-001", "done"))
    forged_event = {
        "version": store.EVENT_VERSION,
        "type": store.EVENT_TYPE_STATE_COMMITTED,
        "sequence": 1,
        "committed_at": "2026-07-27T00:00:00Z",
        "source": "forged-checkpoint-only",
        "previous_event_sha256": None,
        "state_sha256": store.sha256_json(forged_state),
        "state": forged_state,
    }
    event_sha256 = store.sha256_json(forged_event)
    forged_event["event_sha256"] = event_sha256
    forged_event["event_id"] = f"task-state-{event_sha256}"
    store.validate_event(
        forged_event,
        expected_sequence=1,
        previous_sha256=None,
    )

    # The journal and its exact prefix digest stay untouched. Only the derived
    # cache is edited, using a fully self-consistent forged event.
    journal_before = path.read_bytes()
    checkpoint["last_event"] = forged_event
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    accelerated = store.load_snapshot(path)

    assert path.read_bytes() == journal_before
    assert accelerated["resumed_from_checkpoint"] is False
    assert accelerated["state"] == real_state
    assert accelerated["state"] != forged_state
    repaired_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert repaired_checkpoint["last_event"]["state"] == real_state


def test_append_does_not_replay_the_journal_to_read_back_its_own_line(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "task-state-events.jsonl"
    for index in range(1, 4):
        store.append_state_commit(path, growing_board(index), source="test")

    passes = _replays(monkeypatch)
    store.append_state_commit(path, growing_board(4), source="test")

    # One snapshot to establish the previous state; the readback verifies the
    # appended bytes at their offset instead of replaying the whole file.
    assert passes == ["checkpoint"]


def test_read_then_commit_costs_one_journal_pass(tmp_path: Path, monkeypatch) -> None:
    """The shape of a status command: project the board, then commit one change."""

    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, board(("T-0", "todo")), source="test")

    passes = _replays(monkeypatch)
    current = store.load_snapshot(path)["state"]
    current["tasks"][0]["status"] = "in_progress"
    store.append_state_commit(path, current, source="ai-status")

    assert passes == ["checkpoint", "checkpoint"]
    assert store.load_snapshot(path)["state"]["tasks"][0]["status"] == "in_progress"


def test_append_readback_detects_a_short_write(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "task-state-events.jsonl"
    store.append_state_commit(path, board(("T-0", "todo")), source="test")

    real_write = os.write

    def truncating(fd: int, data: bytes) -> int:
        # Report a full write while only part of the payload lands.
        real_write(fd, data[: max(1, len(data) // 2)])
        return len(data)

    monkeypatch.setattr(store.os, "write", truncating)
    with pytest.raises(store.TaskStateStoreError, match="append readback mismatch"):
        store.append_state_commit(path, board(("T-0", "in_progress")), source="test")


def test_verify_snapshot_reports_one_generation(tmp_path: Path) -> None:
    """A report is built from a single snapshot, so it cannot straddle commits."""

    path = tmp_path / "task-state-events.jsonl"
    first = board(("T-0", "todo"))
    store.append_state_commit(path, first, source="test")
    snapshot = store.load_snapshot(path)

    # A commit lands after the snapshot was taken.
    store.append_state_commit(path, board(("T-0", "in_progress")), source="test")
    report = store.verify_snapshot(snapshot, first)

    assert report["ok"] is True
    assert report["event_count"] == 1
    assert report["last_event_id"] == snapshot["last_event_id"]
    assert report["projected_state_sha256"] == report["expected_state_sha256"]
