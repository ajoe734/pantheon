"""Retired commands cannot enqueue work or emit manufactured owner results."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters.retired import RETIRED_COMMANDS
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import OperatorIdentity


@pytest.mark.parametrize("command", RETIRED_COMMANDS)
def test_retired_mounted_command_has_no_effect(tmp_path, command):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    executed = []

    def identity(auth, **kwargs):
        if auth != "Bearer test":
            raise HTTPException(401)
        return OperatorIdentity(operator_id="operator", roles=["admin"], mfa_verified=True)

    app = FastAPI()
    app.include_router(create_command_adapters_router(service=CommandAdapterService(
        command_store=store, extract_identity=identity,
        process_command_task=executed.append,
    )))
    client = TestClient(app)
    payload = {"command": command, "target": {"type": "Review", "id": "target"}}
    assert client.post("/bff/v1/commands", json=payload).status_code == 401
    for _ in range(2):
        response = client.post("/bff/v1/commands", json=payload, headers={
            "Authorization": "Bearer test", "Idempotency-Key": "same-key",
        })
        assert response.status_code == 410, response.text
        assert RETIRED_COMMANDS[command] in response.text
    assert store.get_command_by_idempotency_key("same-key", operator_id="operator") is None
    assert not executed
