from __future__ import annotations

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from models import ObjectType


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


def test_two_man_sign_concurrent_operators_admit_only_one_command() -> None:
    """Concurrent two-man-sign aliases for one intervention must not double-admit."""
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
                    "reason": "concurrent second-operator signature",
                    "secondOperatorId": f"second-op-{index}",
                },
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(sign, enumerate((OPERATOR_TOKEN, ADMIN_TOKEN))))

        statuses = sorted(status for status, _body in results)
        assert statuses == [202, 409]
        rejected = [body for status, body in results if status == 409]
        error = (rejected[0].get("detail") or rejected[0]).get("error")
        assert error["code"] == "RESOURCE_CONFLICT"
        assert error["details"]["precondition_failed"] == "concurrent_safety"

        records = bff_main.command_store._get_all_commands()
        assert len(records) == 1
        assert records[0]["type"] == "V5InterventionAction"
        assert records[0]["target"] == {
            "type": ObjectType.SENTINEL_INTERVENTION.value,
            "id": "intv-two-man-race",
        }
