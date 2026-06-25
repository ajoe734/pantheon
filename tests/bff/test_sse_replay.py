from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = ROOT / "services" / "control-plane" / "bff"
BFF_MAIN = BFF_DIR / "main.py"

AUTH = "Bearer op-ha008:operator,approver,admin,reviewer:mfa"


def bff_env(data_dir: Path) -> dict[str, str]:
    return {
        "BFF_DATA_DIR": str(data_dir),
        "PANTHEON_BFF_AUTH_STUB": "true",
        "PANTHEON_BFF_AUTH_MODE": "permissive",
        "PANTHEON_BFF_SSE_REPLAY_STORE": "file",
        "PANTHEON_ENV": "dev",
        "PANTHEON_DEPLOYMENT_STAGE": "dev",
        "PANTHEON_BFF_JWT_SECRET": "ha-008-v2-test-secret",
    }


@contextmanager
def patched_env(**values: str) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def load_bff_replica(name: str, data_dir: Path) -> ModuleType:
    if str(BFF_DIR) not in sys.path:
        sys.path.insert(0, str(BFF_DIR))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    with patched_env(**bff_env(data_dir)):
        module_name = f"_ha008_bff_replica_{name}_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, BFF_MAIN)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


def publish_approval_event(
    replica: ModuleType,
    sequence_no: int,
    *,
    causal_parent_id: str | None = None,
) -> str:
    return replica._publish_event(
        replica._sse_buffers["approval"],
        replica._sse_subscribers["approval"],
        "approval.stage.changed",
        {
            "approval_id": "appr-ha-008-v2",
            "aggregate_type": "approval decision",
            "aggregate_id": "appr-ha-008-v2",
            "sequence_no": sequence_no,
            "causal_parent_id": causal_parent_id,
            "metadata": {"task_id": "HA-008-V2"},
        },
    )


def parse_sse_chunk(chunk: str) -> dict[str, Any]:
    fields: dict[str, list[str]] = {}
    for line in chunk.splitlines():
        if not line or line.startswith(":"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.strip(), []).append(value.lstrip())
    return {
        "id": fields.get("id", [""])[-1],
        "event": fields.get("event", [""])[-1],
        "data": json.loads("\n".join(fields.get("data", []))),
    }


async def replay_from_replica(
    replica: ModuleType,
    last_event_id: str,
    *,
    expected_count: int,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    response = await replica.stream_generic_events("approval", last_event_id, AUTH)
    iterator = response.body_iterator
    chunks: list[str] = []
    try:
        for _ in range(expected_count):
            chunks.append(await asyncio.wait_for(anext(iterator), timeout=1.0))
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
    return dict(response.headers), [parse_sse_chunk(chunk) for chunk in chunks]


def test_last_event_id_replay_survives_replica_failover_without_gap_or_duplicate() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "bff-data"
        with patched_env(**bff_env(data_dir)):
            primary = load_bff_replica("primary", data_dir)
            passive = load_bff_replica("passive", data_dir)

            first_id = publish_approval_event(primary, 1)
            second_id = publish_approval_event(primary, 2, causal_parent_id=first_id)
            third_id = publish_approval_event(primary, 3, causal_parent_id=second_id)

            assert len(passive._sse_buffers["approval"]) == 0

            headers, replayed = asyncio.run(
                replay_from_replica(passive, first_id, expected_count=2)
            )

    replayed_ids = [event["id"] for event in replayed]
    sequence_numbers = [event["data"]["data"]["sequence_no"] for event in replayed]

    assert headers["x-sse-replay-store"] == "file"
    assert replayed_ids == [second_id, third_id]
    assert sequence_numbers == [2, 3]
    assert first_id not in replayed_ids
    assert len(replayed_ids) == len(set(replayed_ids))


def test_shared_sse_replay_fails_closed_when_cursor_is_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / "bff-data"
        with patched_env(**bff_env(data_dir)):
            replica = load_bff_replica("solo", data_dir)
            publish_approval_event(replica, 1)

            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(
                    replay_from_replica(
                        replica,
                        "evt-ha-008-v2-missing",
                        expected_count=1,
                    )
                )

    error = exc_info.value
    detail = error.detail

    assert error.status_code == 409
    assert error.headers["X-SSE-Replay-Store"] == "file"
    assert detail["error"]["code"] == "SSE_REPLAY_UNAVAILABLE"
    assert detail["error"]["details"]["reason"] == "SSE_REPLAY_HISTORY_MISSING"
    assert detail["error"]["details"]["lastEventId"] == "evt-ha-008-v2-missing"
    assert detail["error"]["details"]["channel"] == "approval"
