from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from .. import command_idempotency as command_idempotency_module
from ..command_idempotency import (
    CommandIdempotencyHeaderConflict,
    CommandIdempotencyInProgress,
    CommandIdempotencyKeyRequired,
    CommandIdempotencyPayloadConflict,
    CommandIdempotencyRecoveryRequired,
    CommandIdempotencyStorageError,
    CommandIdempotencyStore,
    resolve_command_idempotency_key,
)


def test_header_resolution_rejects_conflicts_and_required_missing() -> None:
    assert resolve_command_idempotency_key("canonical", None) == "canonical"
    assert resolve_command_idempotency_key(None, "alias") == "alias"
    assert resolve_command_idempotency_key("same", "same") == "same"
    with pytest.raises(CommandIdempotencyHeaderConflict):
        resolve_command_idempotency_key("one", "two")
    with pytest.raises(CommandIdempotencyKeyRequired):
        resolve_command_idempotency_key(None, None, required=True)


def test_exact_replay_survives_reload_without_persisting_request_secrets(tmp_path: Path) -> None:
    path = tmp_path / "idempotency.json"
    payload = {"passphrase": "never-persist-this", "mode": "kernel_debug"}
    store = CommandIdempotencyStore(str(path))

    with store.transaction(
        actor_id="operator-secret-name",
        route="/bff/assistant/control-mode/activate",
        idempotency_key="client-secret-key",
        request_payload=payload,
    ) as transaction:
        assert transaction.replayed is False
        transaction.complete({"data": {"activationId": "activation-stable"}})

    reloaded = CommandIdempotencyStore(str(path))
    with reloaded.transaction(
        actor_id="operator-secret-name",
        route="/bff/assistant/control-mode/activate",
        idempotency_key="client-secret-key",
        request_payload=payload,
    ) as transaction:
        assert transaction.replayed is True
        assert transaction.response == {"data": {"activationId": "activation-stable"}}

    stored = path.read_text(encoding="utf-8")
    assert "never-persist-this" not in stored
    assert "operator-secret-name" not in stored
    assert "client-secret-key" not in stored
    assert "/bff/assistant/control-mode/activate" not in stored
    assert (path.stat().st_mode & 0o777) == 0o600


def test_same_key_different_payload_conflicts(tmp_path: Path) -> None:
    store = CommandIdempotencyStore(str(tmp_path / "idempotency.json"))
    with store.transaction(
        actor_id="operator",
        route="/route",
        idempotency_key="same-key",
        request_payload={"value": 1},
    ) as transaction:
        transaction.complete({"data": {"ok": True}})

    with pytest.raises(CommandIdempotencyPayloadConflict):
        with store.transaction(
            actor_id="operator",
            route="/route",
            idempotency_key="same-key",
            request_payload={"value": 2},
        ):
            pass


def test_same_client_key_is_scoped_by_actor_and_route(tmp_path: Path) -> None:
    store = CommandIdempotencyStore(str(tmp_path / "idempotency.json"))
    responses = []
    for actor_id, route in (
        ("operator-a", "/route-a"),
        ("operator-b", "/route-a"),
        ("operator-a", "/route-b"),
    ):
        with store.transaction(
            actor_id=actor_id,
            route=route,
            idempotency_key="shared-client-key",
            request_payload={"same": True},
        ) as transaction:
            assert transaction.replayed is False
            response = {"data": {"actor": actor_id, "route": route}}
            transaction.complete(response)
            responses.append(response)

    assert len(responses) == 3


def test_concurrent_exact_requests_execute_once(tmp_path: Path) -> None:
    path = tmp_path / "idempotency.json"
    start = threading.Barrier(3)
    operation_count = 0
    operation_lock = threading.Lock()
    responses: list[dict] = []

    def invoke() -> None:
        nonlocal operation_count
        store = CommandIdempotencyStore(str(path))
        start.wait()
        with store.transaction(
            actor_id="operator",
            route="/route",
            idempotency_key="concurrent-key",
            request_payload={"same": True},
        ) as transaction:
            if transaction.replayed:
                response = transaction.response or {}
            else:
                with operation_lock:
                    operation_count += 1
                time.sleep(0.05)
                response = {"data": {"operation": "one"}}
                transaction.complete(response)
            responses.append(response)

    threads = [threading.Thread(target=invoke), threading.Thread(target=invoke)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert operation_count == 1
    assert responses == [
        {"data": {"operation": "one"}},
        {"data": {"operation": "one"}},
    ]


def test_uncertain_operation_fails_closed_until_exact_explicit_recovery(tmp_path: Path) -> None:
    store = CommandIdempotencyStore(
        str(tmp_path / "idempotency.json"),
        recovery_seconds=0,
    )
    request = {"scope": ["services/control-plane/bff"]}
    with pytest.raises(RuntimeError, match="simulated crash"):
        with store.transaction(
            actor_id="operator",
            route="/repair",
            idempotency_key="uncertain-key",
            request_payload=request,
        ):
            raise RuntimeError("simulated crash")

    with pytest.raises(CommandIdempotencyRecoveryRequired):
        with store.transaction(
            actor_id="operator",
            route="/repair",
            idempotency_key="uncertain-key",
            request_payload=request,
        ):
            pass

    store.recover_uncertain(
        actor_id="operator",
        route="/repair",
        idempotency_key="uncertain-key",
        request_payload=request,
        recovery_id="incident-2026-07-15-001",
    )
    with store.transaction(
        actor_id="operator",
        route="/repair",
        idempotency_key="uncertain-key",
        request_payload=request,
    ) as transaction:
        transaction.complete({"data": {"recovered": True}})


def test_uncertain_recovery_cannot_bypass_bounded_delay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(command_idempotency_module.time, "time", lambda: now[0])
    store = CommandIdempotencyStore(
        str(tmp_path / "idempotency.json"),
        recovery_seconds=30,
    )
    request = {"scope": ["services/control-plane/bff"]}
    with pytest.raises(RuntimeError, match="simulated crash"):
        with store.transaction(
            actor_id="operator",
            route="/repair",
            idempotency_key="bounded-recovery-key",
            request_payload=request,
        ):
            raise RuntimeError("simulated crash")

    with pytest.raises(CommandIdempotencyInProgress):
        store.recover_uncertain(
            actor_id="operator",
            route="/repair",
            idempotency_key="bounded-recovery-key",
            request_payload=request,
            recovery_id="incident-too-early",
        )

    now[0] = 1031.0
    store.recover_uncertain(
        actor_id="operator",
        route="/repair",
        idempotency_key="bounded-recovery-key",
        request_payload=request,
        recovery_id="incident-after-delay",
    )
    with store.transaction(
        actor_id="operator",
        route="/repair",
        idempotency_key="bounded-recovery-key",
        request_payload=request,
    ) as transaction:
        transaction.complete({"data": {"recovered": True}})

    persisted = (tmp_path / "idempotency.json").read_text(encoding="utf-8")
    assert "incident-after-delay" not in persisted


def test_corrupt_store_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "idempotency.json"
    path.write_text("{not-json", encoding="utf-8")
    store = CommandIdempotencyStore(str(path))
    with pytest.raises(CommandIdempotencyStorageError):
        with store.transaction(
            actor_id="operator",
            route="/route",
            idempotency_key="key",
            request_payload={"safe": True},
        ):
            pass


def test_persisted_document_contains_only_digests_and_replay_response(tmp_path: Path) -> None:
    path = tmp_path / "idempotency.json"
    store = CommandIdempotencyStore(str(path))
    with store.transaction(
        actor_id="operator",
        route="/route",
        idempotency_key="key",
        request_payload={"credential": "do-not-store"},
    ) as transaction:
        transaction.complete({"data": {"safe": "response"}})

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["version"] == 1
    record = next(iter(document["records"].values()))
    assert set(record) == {"status", "request_hash", "completed_at", "response"}
    assert len(record["request_hash"]) == 64
