from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_queue import CommandStore


HEADERS = {"Authorization": "Bearer op-bff-b1-009:operator,approver:mfa"}


@contextmanager
def _isolated_confirm_tokens() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        store = CommandStore(os.path.join(td, "commands.jsonl"))
        app = FastAPI()
        router = create_command_adapters_router(
            get_command_store=lambda: store,
            get_read_store=lambda: None,
        )
        app.include_router(router)

        @app.exception_handler(HTTPException)
        def _exc_handler(request: Request, exc: HTTPException):
            content = exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail)}}
            return JSONResponse(status_code=exc.status_code, content=content)

        yield TestClient(app)


def test_confirm_token_and_command_confirmation_lifecycle() -> None:
    with _isolated_confirm_tokens() as client:
        create = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "bff-b1-009-create"},
            json={"tokenId": "ct-bff-b1-009", "reason": "guarded action", "ttlSeconds": 60},
        )
        assert create.status_code == 201, create.text
        assert create.json()["data"]["id"] == "ct-bff-b1-009"
        assert create.json()["data"]["tokenId"] == "ct-bff-b1-009"
        assert create.json()["data"]["status"] == "created"
        assert create.json()["meta"]["idempotency"]["replayed"] is False

        read_created = client.get("/bff/confirm-tokens/ct-bff-b1-009", headers=HEADERS)
        assert read_created.status_code == 200, read_created.text
        assert read_created.json()["data"]["status"] == "created"
        assert read_created.json()["data"]["expired"] is False

        redeem = client.post(
            "/bff/confirm-tokens/ct-bff-b1-009/redeem",
            headers={**HEADERS, "Idempotency-Key": "bff-b1-009-redeem"},
            json={"reason": "operator confirmed"},
        )
        assert redeem.status_code == 202, redeem.text
        assert redeem.json()["status"] == "accepted"
        assert redeem.json()["data"]["tokenId"] == "ct-bff-b1-009"
        assert redeem.json()["data"]["status"] == "redeemed"
        assert redeem.json()["data"]["redeemed"] is True
        assert redeem.json()["data"]["receipt"]["status"] == "accepted"

        create_for_confirmation = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "bff-b1-009-create-confirmation"},
            json={"tokenId": "ct-bff-b1-009-confirmation", "reason": "legacy confirmation"},
        )
        assert create_for_confirmation.status_code == 201, create_for_confirmation.text

        confirmation = client.post(
            "/bff/command-confirmations",
            headers={**HEADERS, "Idempotency-Key": "bff-b1-009-command-confirmation"},
            json={
                "command_id": "cmd-bff-b1-009",
                "confirm_token": "ct-bff-b1-009-confirmation",
            },
        )
        assert confirmation.status_code == 202, confirmation.text
        assert confirmation.json()["command_id"] == "cmd-bff-b1-009"
        assert confirmation.json()["tokenId"] == "ct-bff-b1-009-confirmation"
        assert confirmation.json()["status"] == "accepted"
        assert confirmation.json()["lifecycleStatus"] == "redeemed"
        assert confirmation.json()["redeemed"] is True

        confirmation_status = client.get(
            "/bff/command-confirmations/ct-bff-b1-009-confirmation",
            headers=HEADERS,
        )
        assert confirmation_status.status_code == 200, confirmation_status.text
        assert confirmation_status.json()["data"]["status"] == "redeemed"
        assert confirmation_status.json()["data"]["lifecycleStatus"] == "redeemed"
        assert confirmation_status.json()["data"]["command_id"] == "cmd-bff-b1-009"
        assert confirmation_status.json()["data"]["redeemed"] is True

        mirrored_token_status = client.get(
            "/bff/confirm-tokens/ct-bff-b1-009-confirmation",
            headers=HEADERS,
        )
        assert mirrored_token_status.status_code == 200, mirrored_token_status.text
        assert mirrored_token_status.json()["data"]["status"] == "redeemed"


def test_expired_confirm_tokens_return_typed_410() -> None:
    with _isolated_confirm_tokens() as client:
        create = client.post(
            "/bff/confirm-tokens",
            headers={**HEADERS, "Idempotency-Key": "bff-b1-009-expired-create"},
            json={"tokenId": "ct-bff-b1-009-expired", "ttlSeconds": -1},
        )
        assert create.status_code == 201, create.text

        for method, path, body in [
            ("GET", "/bff/confirm-tokens/ct-bff-b1-009-expired", None),
            ("POST", "/bff/confirm-tokens/ct-bff-b1-009-expired/redeem", {"reason": "too late"}),
            (
                "POST",
                "/bff/command-confirmations",
                {"command_id": "cmd-expired", "confirm_token": "ct-bff-b1-009-expired"},
            ),
            ("GET", "/bff/command-confirmations/ct-bff-b1-009-expired", None),
        ]:
            response = client.request(
                method,
                path,
                headers={**HEADERS, "Idempotency-Key": f"bff-b1-009-expired-{method}-{path}"},
                json=body,
            )
            assert response.status_code == 410, response.text
            detail = response.json()
            assert detail["error"]["code"] == "OPERATION_NOT_ALLOWED"
            assert detail["error"]["details"]["precondition_failed"] == "confirm_token_expired"
            assert detail["error"]["details"]["tokenId"] == "ct-bff-b1-009-expired"
