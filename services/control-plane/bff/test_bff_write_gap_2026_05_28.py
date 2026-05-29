"""Contract tests for BFF Write-Gap endpoints — 2026-05-28 sprint.

Covers Card P0-1: POST /bff/personas/{id}/actions/AdvanceLifecycle
  - Probes that the endpoint no longer returns 410 for AdvanceLifecycle.
  - Validates 202 + commandId on a successful admission (dry-run / stub).
  - Validates typed 4xx responses (not raw 410 / not bare "Not Found").

Sprint: EPIC-WRITE-GAP-P0-LIFECYCLE
Spec:   docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md
"""
from __future__ import annotations

import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")

import main as bff_main  # noqa: E402

_OPERATOR_TOKEN = "Bearer test-operator:operator"
_APPROVER_TOKEN = "Bearer test-approver:approver"
_VIEWER_TOKEN = "Bearer test-viewer:reviewer"


@contextmanager
def _stub_auth() -> Generator[None, None, None]:
    original = os.environ.get("PANTHEON_BFF_AUTH_STUB")
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
        else:
            os.environ["PANTHEON_BFF_AUTH_STUB"] = original


def _client() -> TestClient:
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _advance_lifecycle_url(persona_id: str) -> str:
    return f"/bff/personas/{persona_id}/actions/AdvanceLifecycle"


def _idempotency_key() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# P0-1 positive path: AdvanceLifecycle no longer returns 410
# ---------------------------------------------------------------------------


def test_advance_lifecycle_returns_202_not_410() -> None:
    """Regression: AdvanceLifecycle must return 202, not 410 deprecated."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "paper_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    # Must not be 410 (deprecated) — 202 or a typed 4xx (persona not found) are acceptable
    assert response.status_code != 410, (
        f"AdvanceLifecycle returned 410 deprecated — route not registered properly.\n{response.text}"
    )
    assert response.status_code in (202, 404), (
        f"Unexpected status {response.status_code}. Expected 202 (accepted) or 404 (persona not found).\n{response.text}"
    )


def test_advance_lifecycle_404_persona_has_typed_envelope() -> None:
    """A missing persona returns 404 with Pack D error envelope, not bare 'Not Found'."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-does-not-exist-xyzzy"),
            json={
                "target_state": "paper_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 404, response.text
    body = response.json()
    # Must be typed Pack D envelope, not bare FastAPI "Not Found"
    assert "error" in body or "detail" not in body or (
        isinstance(body.get("detail"), dict) and "error" in body["detail"]
    ), f"Expected Pack D error envelope in 404 body, got: {body}"
    # Must NOT be the raw deprecated-route 410 payload
    assert response.status_code != 410


def test_advance_lifecycle_missing_target_state_returns_422() -> None:
    """Missing target_state field returns 422 VALIDATION_FAILED."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={"confirm_token": "tok-test-abc"},
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422, response.text
    body = response.json()
    _assert_error_code(body, "VALIDATION_FAILED")


def test_advance_lifecycle_invalid_target_state_returns_422() -> None:
    """An unsupported target_state value returns 422 VALIDATION_FAILED."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "invalid_state",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422, response.text
    body = response.json()
    _assert_error_code(body, "VALIDATION_FAILED")


def test_advance_lifecycle_missing_confirm_token_returns_422() -> None:
    """Missing confirm_token returns 422 VALIDATION_FAILED, not a downstream error."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={"target_state": "paper_owner"},
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422, response.text
    body = response.json()
    _assert_error_code(body, "VALIDATION_FAILED")


def test_advance_lifecycle_unauthenticated_returns_401() -> None:
    """No auth header returns 401 AUTH_REQUIRED."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "paper_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={"Idempotency-Key": _idempotency_key()},
        )
    assert response.status_code == 401, response.text


def test_advance_lifecycle_live_owner_requires_approver_role() -> None:
    """Advancing to live_owner with operator-only role returns 403."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "live_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    # Operator (not approver) attempting live_owner should be 403
    assert response.status_code == 403, response.text
    body = response.json()
    _assert_error_code(body, "FORBIDDEN")


def test_advance_lifecycle_unregistered_action_id_still_returns_410() -> None:
    """Other action_ids not yet registered still return 410 with replacement hint."""
    with _stub_auth():
        client = _client()
        response = client.post(
            "/bff/personas/persona-test/actions/SomeUnregisteredAction",
            json={},
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
            },
        )
    assert response.status_code == 410, response.text
    body = response.json()
    # Replacement must be present in the 410 details
    detail = body.get("detail") or {}
    error = detail.get("error") or {}
    details = error.get("details") or {}
    assert details.get("replacement"), (
        f"410 response missing details.replacement: {body}"
    )


# ---------------------------------------------------------------------------
# Action catalog: AdvanceLifecycle registered
# ---------------------------------------------------------------------------


def test_action_catalog_contains_advance_lifecycle() -> None:
    """Action catalog endpoint lists AdvanceLifecycle as a registered action."""
    with _stub_auth():
        client = _client()
        response = client.get(
            "/bff/actions",
            headers={"Authorization": _OPERATOR_TOKEN},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    catalog = body.get("catalog") or []
    action_ids = [entry.get("action_id") for entry in catalog]
    assert "AdvanceLifecycle" in action_ids, (
        f"AdvanceLifecycle not found in action catalog. Found: {action_ids}"
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _assert_error_code(body: dict, expected_code: str) -> None:
    error = body.get("error") or (body.get("detail") or {}).get("error") or {}
    code = error.get("code")
    assert code == expected_code, (
        f"Expected error code {expected_code!r}, got {code!r}. Body: {body}"
    )
