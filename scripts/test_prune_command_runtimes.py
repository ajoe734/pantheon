from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import prune_command_runtimes as prune


def _make_sha_dir(parent: Path, sha: str, *, mtime: float, sealed: bool = False) -> Path:
    entry = parent / sha
    entry.mkdir(parents=True)
    (entry / "marker.txt").write_text("x", encoding="utf-8")
    os.utime(entry / "marker.txt", (mtime, mtime))
    os.utime(entry, (mtime, mtime))
    if sealed:
        (entry / "marker.txt").chmod(0o444)
        entry.chmod(0o555)
    return entry


def _live_config(path: Path, live_root: Path) -> None:
    payload = {
        "watchdog": {
            "supervisor_command": [
                "python3",
                str(live_root / ".orchestrator" / "supervisor.py"),
            ]
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40
SHA_E = "e" * 40
SHA_F = "f" * 40


def test_plan_deletions_keeps_live_newest_and_leased(tmp_path: Path) -> None:
    parent = tmp_path / "command-runtimes"
    parent.mkdir()
    # Oldest to newest.
    _make_sha_dir(parent, SHA_A, mtime=1)
    _make_sha_dir(parent, SHA_B, mtime=2)
    _make_sha_dir(parent, SHA_C, mtime=3)
    _make_sha_dir(parent, SHA_D, mtime=4)
    _make_sha_dir(parent, SHA_E, mtime=5)
    _make_sha_dir(parent, SHA_F, mtime=6)

    live_root = str((parent / SHA_A).resolve())  # deliberately the oldest
    leased_roots = {str((parent / SHA_B).resolve())}  # also old, but a worker still owns it

    delete, retain = prune.plan_deletions(
        parent, keep=2, live_root=live_root, leased_roots=leased_roots
    )

    retained_names = {p.name for p in retain}
    deleted_names = {p.name for p in delete}
    assert retained_names == {SHA_A, SHA_B, SHA_E, SHA_F}
    assert deleted_names == {SHA_C, SHA_D}


def test_plan_deletions_ignores_non_sha_entries(tmp_path: Path) -> None:
    parent = tmp_path / "command-runtimes"
    parent.mkdir()
    _make_sha_dir(parent, SHA_A, mtime=1)
    (parent / ".runtime-materialize-abc.XXXXXX").mkdir()
    (parent / "not-a-sha").mkdir()

    delete, retain = prune.plan_deletions(parent, keep=5, live_root=None, leased_roots=set())

    assert {p.name for p in retain} == {SHA_A}
    assert delete == []


def test_unseal_and_delete_removes_readonly_checkout(tmp_path: Path) -> None:
    parent = tmp_path / "command-runtimes"
    parent.mkdir()
    entry = _make_sha_dir(parent, SHA_A, mtime=1, sealed=True)
    assert entry.exists()

    prune._unseal_and_delete(entry)

    assert not entry.exists()


def _write_runtime_state(status_root: Path, workers: dict) -> None:
    orchestrator_dir = status_root / ".orchestrator"
    orchestrator_dir.mkdir(parents=True, exist_ok=True)
    events: dict[str, dict] = {}
    for run_id, worker in workers.items():
        status = str(worker.get("status") or "").strip().lower()
        if status in {"queued", "started", "running", "retry_backoff", "stalled", "admitted"}:
            event_id = f"evt-{run_id}"
            worker["queue_event_id"] = event_id
            events[event_id] = {"intent": {"kind": "dispatch"}}
    state = {"version": 2, "workers": workers, "queue": {"events": events}}
    (orchestrator_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (status_root / "ai-status.json").write_text('{"tasks": []}', encoding="utf-8")


def test_active_leased_roots_only_counts_running_workers(tmp_path: Path) -> None:
    status_root = tmp_path / "coordination"
    leased_root = tmp_path / "command-runtimes" / SHA_A
    leased_root.mkdir(parents=True)
    done_root = tmp_path / "command-runtimes" / SHA_B
    done_root.mkdir(parents=True)

    _write_runtime_state(
        status_root,
        {
            "run-1": {
                "status": "running",
                "status_command_runtime": {"command_root": str(leased_root)},
            },
            "run-2": {
                "status": "done",
                "status_command_runtime": {"command_root": str(done_root)},
            },
        },
    )

    roots = prune._active_leased_roots(status_root)

    assert roots == {str(leased_root.resolve())}


def test_main_dry_run_does_not_delete(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parent = tmp_path / "command-runtimes"
    parent.mkdir()
    _make_sha_dir(parent, SHA_A, mtime=1)
    _make_sha_dir(parent, SHA_B, mtime=2)

    live_config = tmp_path / "live-config.json"
    _live_config(live_config, parent / SHA_B)

    status_root = tmp_path / "coordination"
    _write_runtime_state(status_root, {})

    rc = prune.main(
        [
            "--parent",
            str(parent),
            "--live-config",
            str(live_config),
            "--status-root",
            str(status_root),
            "--keep",
            "1",
            "--dry-run",
            "--json",
        ]
    )

    assert rc == 0
    assert (parent / SHA_A).exists()
    assert (parent / SHA_B).exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["deleted"] == []
    assert Path(payload["planned"][0]).name == SHA_A


def test_main_deletes_and_keeps_live_and_leased(tmp_path: Path) -> None:
    parent = tmp_path / "command-runtimes"
    parent.mkdir()
    _make_sha_dir(parent, SHA_A, mtime=1, sealed=True)
    _make_sha_dir(parent, SHA_B, mtime=2, sealed=True)
    _make_sha_dir(parent, SHA_C, mtime=3, sealed=True)

    live_config = tmp_path / "live-config.json"
    _live_config(live_config, parent / SHA_C)

    status_root = tmp_path / "coordination"
    _write_runtime_state(
        status_root,
        {
            "run-1": {
                "status": "running",
                "status_command_runtime": {"command_root": str(parent / SHA_A)},
            },
        },
    )

    rc = prune.main(
        [
            "--parent",
            str(parent),
            "--live-config",
            str(live_config),
            "--status-root",
            str(status_root),
            "--keep",
            "1",
        ]
    )

    assert rc == 0
    assert (parent / SHA_A).exists()  # leased
    assert (parent / SHA_B).exists() is False  # neither live, leased, nor newest
    assert (parent / SHA_C).exists()  # live
