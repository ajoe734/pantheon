from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from command_queue import CommandStore
from models import CommandStatus, ObjectType


OPERATOR_TOKEN = "Bearer two-man-race-op:operator"
ADMIN_TOKEN = "Bearer two-man-race-admin:admin:mfa"


@contextmanager
def _isolated_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_final_idem = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
        try:
            yield TestClient(bff_main.app, raise_server_exceptions=False)
        finally:
            bff_main.command_store = original_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idem)


def test_two_man_sign_concurrent_operators_record_both_authenticated_signers() -> None:
    """Concurrent calls must atomically preserve both authenticated signers."""
    with _isolated_client():
        def sign(index_and_token: tuple[int, str]):
            index, token = index_and_token
            local_client = TestClient(bff_main.app, raise_server_exceptions=False)
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

        records = bff_main.command_store._get_all_commands()
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
