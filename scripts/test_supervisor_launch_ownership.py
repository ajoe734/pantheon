from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_remote_helper_is_diagnostic_only() -> None:
    source = (ROOT / "scripts" / "remote_orchestrator.sh").read_text(
        encoding="utf-8"
    )
    for retired in (
        "run-supervisor.sh",
        "start_supervisor",
        "stop_repo_writers",
        "tmux kill-session",
        "kill -9",
        "-name '*.pid'",
    ):
        assert retired not in source
    assert 'status)' in source
    assert 'logs|tail)' in source
    assert "immutable runtime promotion and the watchdog" in source


def test_credential_coupled_restart_launcher_is_removed() -> None:
    assert not (ROOT / "scripts" / "shioaji-restart-with-env.sh").exists()


def test_local_launcher_delegates_live_identity_to_canonical_entry_guard() -> None:
    source = (ROOT / "scripts" / "run-supervisor.sh").read_text(encoding="utf-8")
    assert 'exec python3 "$ROOT_DIR/.orchestrator/supervisor.py" "$@"' in source
    assert "promoted/live operation belongs exclusively" in source
