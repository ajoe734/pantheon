from __future__ import annotations

import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.models import (
    CommandStatus,
    CommandType,
    ObjectType,
    OperatorIdentity,
    TargetObject,
)

OPERATOR_TOKEN = "Bearer two-man-race-op:operator"
ADMIN_TOKEN = "Bearer two-man-race-admin:admin:mfa"


class _StoreHolder:
    store: Optional[CommandStore] = None


_current_holder = _StoreHolder()


def _make_sem_command_submitter(store: CommandStore):
    async def _submit(
        *,
        command_type: CommandType,
        target_type: ObjectType,
        target_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
        terminal_on_persist: bool = False,
        trusted_evidence_producer: Optional[str] = None,
        **kwargs: Any,
    ):
        cmd_id = f"cmd-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        target = TargetObject(type=target_type, id=target_id)
        audit_ctx = {
            "operator_id": identity.operator_id,
            "roles": identity.roles,
        }
        foundation_ctx = {
            "idempotency_record": {"idempotency_key": idempotency_key or x_idempotency_key},
        }
        if terminal_on_persist:
            store.submit_terminal_command_if_no_active_target(
                command_id=cmd_id,
                command_type=command_type,
                target=target,
                submitted_at=now,
                params=payload,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
            )
        else:
            store.submit_command(
                command_id=cmd_id,
                command_type=command_type,
                target=target,
                submitted_at=now,
                params=payload,
                audit_context=audit_ctx,
                foundation_context=foundation_ctx,
            )
        return JSONResponse(
            status_code=202,
            content={
                "command_id": cmd_id,
                "status": "accepted",
                "data": {
                    "command": command_type.value,
                    "target": target.model_dump(),
                },
            },
        )
    return _submit


@contextmanager
def _isolated_client() -> Iterator[FastAPI]:
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        _current_holder.store = store
        app = FastAPI()
        app.include_router(
            create_control_loops_router(submit_sem_command=_make_sem_command_submitter(store))
        )
        try:
            yield app
        finally:
            _current_holder.store = None


def test_two_man_sign_concurrent_operators_record_both_authenticated_signers() -> None:
    """Concurrent calls must atomically preserve both authenticated signers."""
    with _isolated_client() as app:
        def sign(index_and_token: tuple[int, str]):
            index, token = index_and_token
            local_client = TestClient(app, raise_server_exceptions=False)
            response = local_client.post(
                "/bff/v5/interventions/intv-two-man-race/two-man-sign",
                headers={
                    "Authorization": token,
                    "Idempotency-Key": f"two-man-race-{index}",
                },
                json={
                    "twoManSignatureId": "tms-two-man-race",
                    "command": "HumanGateApprove",
                    "target": {
                        "type": "HumanGateItem",
                        "id": "approval:two-man-race",
                    },
                    # Caller-supplied identities are ignored; each record must
                    # contain only the authenticated actor.
                    "signerOperatorIds": ["forged-a", "forged-b"],
                    "reason": "concurrent second-operator signature",
                },
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(sign, enumerate((OPERATOR_TOKEN, ADMIN_TOKEN))))

        statuses = sorted(status for status, _body in results)
        assert statuses == [202, 202]

        records = _current_holder.store._get_all_commands()
        assert len(records) == 2
        assert {record["type"] for record in records} == {"V5InterventionAction"}
        assert {record["status"] for record in records} == {CommandStatus.EXECUTED.value}
        assert {record["target"]["type"] for record in records} == {
            ObjectType.SENTINEL_INTERVENTION.value
        }
        assert {record["target"]["id"] for record in records} == {"tms-two-man-race"}
        assert {
            tuple(record["params"]["signerOperatorIds"])
            for record in records
        } == {("two-man-race-op",), ("two-man-race-admin",)}
