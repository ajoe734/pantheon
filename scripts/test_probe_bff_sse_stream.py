from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_probe_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "probe_bff_sse_stream.py"
    spec = importlib.util.spec_from_file_location("probe_bff_sse_stream", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeSseResponse:
    status = 200
    headers = {
        "Content-Type": "text/event-stream",
        "X-SSE-Channel": "approval",
        "X-SSE-Replay-Supported": "true",
        "X-SSE-Replay-Store": "in-memory",
    }

    def __init__(self, blocks: list[str]) -> None:
        self._lines = iter("".join(blocks).encode("utf-8").splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def readline(self) -> bytes:
        return next(self._lines, b"")


def _event_block(event_id: str, sequence_no: int = 1) -> str:
    payload: dict[str, Any] = {
        "id": event_id,
        "type": "approval.decided",
        "timestamp": "2026-06-14T00:00:00Z",
        "data": {"approval_id": "appr-soak", "sequence_no": sequence_no},
    }
    return (
        f"id: {event_id}\n"
        "event: approval.decided\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    )


def test_stream_reconnect_sequence_advances_cursor_without_duplicates(monkeypatch) -> None:
    probe = _load_probe_module()
    observed_cursors: list[str | None] = []
    replay_by_cursor = {"evt-first": "evt-second", "evt-second": "evt-third"}

    def fake_stream_first_event(**kwargs):
        cursor = kwargs.get("last_event_id")
        observed_cursors.append(cursor)
        event_id = replay_by_cursor.get(cursor)
        return {
            "ok": True,
            "first_event": {
                "data": {"id": event_id},
            },
        }

    monkeypatch.setattr(probe, "stream_first_event", fake_stream_first_event)

    result = probe.stream_reconnect_sequence(
        base_url="https://bff.example.test",
        mode=probe.BEARER_MODE,
        token="token",
        timeout=1.0,
        channel="approval",
        cookie_name="pantheon_session",
        expected_replays=[("evt-first", "evt-second"), ("evt-second", "evt-third")],
    )

    assert observed_cursors == ["evt-first", "evt-second"]
    assert result["ok"] is True
    assert result["attempt_count"] == 2
    assert result["observed_event_ids"] == ["evt-second", "evt-third"]
    assert result["duplicate_event_ids"] == []
    assert result["missing_expected_event_ids"] == []
    assert result["cursors_advanced"] is True


def test_stream_soak_counts_heartbeat_and_expected_replay_event(monkeypatch) -> None:
    probe = _load_probe_module()

    def fake_urlopen(_request, timeout: float):
        assert timeout >= 6.0
        return _FakeSseResponse([
            _event_block("evt-replayed", 2),
            ": heartbeat\n\n",
        ])

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.stream_soak(
        base_url="https://bff.example.test",
        mode=probe.BEARER_MODE,
        token="token",
        timeout=1.0,
        channel="approval",
        cookie_name="pantheon_session",
        seconds=1.0,
        min_heartbeats=1,
        last_event_id="evt-cursor",
        expected_event_ids={"evt-replayed"},
    )

    assert result["ok"] is True
    assert result["blocks"]["heartbeat_count"] == 1
    assert result["blocks"]["duplicate_event_ids"] == []
    assert result["missing_expected_event_ids"] == []


def test_stream_soak_fails_duplicate_replay_ids(monkeypatch) -> None:
    probe = _load_probe_module()

    def fake_urlopen(_request, timeout: float):
        assert timeout >= 6.0
        return _FakeSseResponse([
            _event_block("evt-duplicate", 2),
            _event_block("evt-duplicate", 2),
            ": heartbeat\n\n",
        ])

    monkeypatch.setattr(probe.urllib.request, "urlopen", fake_urlopen)
    result = probe.stream_soak(
        base_url="https://bff.example.test",
        mode=probe.BEARER_MODE,
        token="token",
        timeout=1.0,
        channel="approval",
        cookie_name="pantheon_session",
        seconds=1.0,
        min_heartbeats=1,
        last_event_id="evt-cursor",
        expected_event_ids={"evt-duplicate"},
    )

    assert result["ok"] is False
    assert result["blocks"]["duplicate_event_ids"] == ["evt-duplicate"]


def test_strict_live_evidence_accepts_real_bearer_long_soak(monkeypatch) -> None:
    probe = _load_probe_module()
    monkeypatch.setenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "live-sse-token")
    args = argparse.Namespace(
        strict_live_evidence=True,
        soak_seconds=75.0,
        soak_min_heartbeats=1,
    )

    probe.apply_strict_live_evidence(args)


def test_strict_live_evidence_rejects_dev_jwt_only(monkeypatch) -> None:
    probe = _load_probe_module()
    monkeypatch.delenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "dev-secret-must-not-count")
    args = argparse.Namespace(
        strict_live_evidence=True,
        soak_seconds=75.0,
        soak_min_heartbeats=1,
    )

    try:
        probe.apply_strict_live_evidence(args)
    except SystemExit as exc:
        assert "PANTHEON_BFF_SMOKE_BEARER_TOKEN" in str(exc)
    else:
        raise AssertionError("strict SSE live evidence must reject dev JWT-only auth")


def test_strict_live_evidence_rejects_short_soak(monkeypatch) -> None:
    probe = _load_probe_module()
    monkeypatch.setenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "live-sse-token")
    args = argparse.Namespace(
        strict_live_evidence=True,
        soak_seconds=30.0,
        soak_min_heartbeats=1,
    )

    try:
        probe.apply_strict_live_evidence(args)
    except SystemExit as exc:
        assert "--soak-seconds >= 75" in str(exc)
    else:
        raise AssertionError("strict SSE live evidence must reject short soak windows")
