"""Canonical process-entry authority for the promoted supervisor runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def validate_supervisor_launch_authority(
    config: Mapping[str, Any],
    *,
    supervisor_path: Path,
) -> None:
    """Reject a mutable supervisor entrypoint for a promoted live config.

    Repository-local development configs intentionally omit
    ``watchdog.supervisor_command`` and remain directly runnable. A promoted
    live config always contains that command, and the command's one absolute
    ``.orchestrator/supervisor.py`` path is the sole process-launch authority.
    """

    watchdog = config.get("watchdog")
    settings = watchdog if isinstance(watchdog, Mapping) else {}
    raw_command = settings.get("supervisor_command")
    if raw_command is None:
        return
    if not isinstance(raw_command, list):
        raise RuntimeError("watchdog.supervisor_command must be a list")
    command = [str(value) for value in raw_command if str(value).strip()]
    candidates = [
        Path(value)
        for value in command
        if Path(value).name == "supervisor.py"
        and ".orchestrator" in Path(value).parts
    ]
    if len(candidates) != 1 or not candidates[0].is_absolute():
        raise RuntimeError(
            "watchdog.supervisor_command must name exactly one absolute "
            ".orchestrator/supervisor.py runtime"
        )
    expected = candidates[0].resolve()
    actual = supervisor_path.resolve()
    if expected != actual:
        raise RuntimeError(
            "supervisor launch rejected: promoted live config is owned by "
            f"immutable runtime {expected}, not mutable entrypoint {actual}"
        )
