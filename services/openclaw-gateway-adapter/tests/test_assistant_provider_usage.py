from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_provider_usage import provider_usage_snapshot  # noqa: E402


def _clock() -> datetime:
    return datetime(2026, 6, 28, 10, 30, tzinfo=timezone.utc)


def test_usage_snapshot_reads_provider_env_remaining() -> None:
    usage = provider_usage_snapshot(
        "codex_cli",
        "codex",
        environ={
            "PANTHEON_ASSISTANT_LLM_USAGE_CODEX_CLI_REMAINING": "42",
            "PANTHEON_ASSISTANT_LLM_USAGE_CODEX_CLI_LIMIT": "100",
            "PANTHEON_ASSISTANT_LLM_USAGE_CODEX_CLI_USED": "58",
            "PANTHEON_ASSISTANT_LLM_USAGE_CODEX_CLI_UNIT": "requests",
            "PANTHEON_ASSISTANT_LLM_USAGE_CODEX_CLI_RESET_AT": "2026-06-29T00:00:00Z",
        },
        clock=_clock,
    )

    assert usage["status"] == "available"
    assert usage["source"] == "env"
    assert usage["remaining"] == 42.0
    assert usage["limit"] == 100.0
    assert usage["used"] == 58.0
    assert usage["remaining_percent"] == 42.0
    assert usage["remainingPercent"] == 42.0
    assert usage["unit"] == "requests"
    assert usage["reset_at"] == "2026-06-29T00:00:00Z"


def test_usage_snapshot_reads_json_file_provider_entry(tmp_path: Path) -> None:
    usage_file = tmp_path / "usage.json"
    usage_file.write_text(
        json.dumps(
            {
                "providers": {
                    "claude": {
                        "remaining": 12,
                        "limit": 20,
                        "used": 8,
                        "unit": "messages",
                        "resetAt": "2026-06-28T18:00:00Z",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    usage = provider_usage_snapshot(
        "claude",
        environ={"PANTHEON_ASSISTANT_LLM_USAGE_FILE": usage_file.as_posix()},
        clock=_clock,
    )

    assert usage["status"] == "available"
    assert usage["source"] == "usage_file"
    assert usage["remaining"] == 12.0
    assert usage["limit"] == 20.0
    assert usage["remaining_percent"] == 60.0
    assert usage["resetAt"] == "2026-06-28T18:00:00Z"


def test_usage_snapshot_unknown_when_no_source_configured() -> None:
    usage = provider_usage_snapshot("openclaw", environ={}, clock=_clock)

    assert usage["status"] == "unknown"
    assert usage["source"] == "not_configured"
    assert usage["remaining"] is None
    assert usage["reason"] == "provider_usage_source_not_configured"
    assert usage["checked_at"] == "2026-06-28T10:30:00Z"
