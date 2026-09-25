"""BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: single-owner Management NL seam.

Verifies:
1. The legacy, env-flag-gated idempotency mechanism is fully deleted (no
   ``_MGMT_NL_IDEMPOTENCY`` in-memory dict, no
   ``PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_REQUIRED`` bypass flag
   anywhere in main.py or docker-compose.yml).
2. ``ManagementNlCommandIdempotencyStore`` (via a single composed
   ``ManagementNlUseCase`` instance) is the sole, unconditional command
   admission/replay mechanism for both HTTP transports.
3. ``bff_management_nl_ask`` and ``bff_management_nl_ask_stream`` are both
   coroutine functions and both route through the exact same shared
   admission entry points -- no duplicated admission/replay/provider-
   dispatch logic per transport.
4. Behaviourally: the SSE stream transport durably replays a terminal
   result instead of invoking the provider twice for an exact-duplicate
   idempotency key, returns 409 on a same-key/different-payload conflict,
   and keeps per-tenant isolation.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

import pytest

from services.control_plane.bff.assistant.management_service import ManagementNlUseCase
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    clear_management_nl_sse_buffer,
    get_management_nl_module,
    get_management_nl_read_store,
    set_management_nl_read_store,
)
from services.control_plane.bff.tests.test_management_nl_assistant_provider import (
    FakeProviderClient,
    OPERATOR_HEADERS,
    _clear_provider_env,
    _seeded_client,
)

# BFF-TEST-MIGRATION-REMAINING-IMPORTERS-001: `bff_main` is bound to the real
# `assistant.management_service` module (get_management_nl_module() no
# longer loads main.py). main.py's own module proxy already delegates every
# one of these attributes onto management_service (BFF-MGMT-NL-HELPER-
# EXTRACTION-001), so this file no longer imports main.py at all; the two
# "legacy mechanism is deleted from main.py's own source" assertions below
# read main.py's text directly via Path instead of importing it.
bff_main = get_management_nl_module()

REPO_ROOT = Path(__file__).resolve().parents[4]
MAIN_PY_PATH = REPO_ROOT / "services" / "control-plane" / "bff" / "main.py"


@pytest.fixture(autouse=True)
def _management_nl_command_idempotency_default_path(monkeypatch, tmp_path):
    """BFF-MANAGEMENT-NL-SEAM-CORRECTIVE-001: durable admission via
    ManagementNlCommandIdempotencyStore is unconditional for both nl/ask
    transports; give it a writable per-test default path since the module
    default (/data/bff/...) does not exist in the test sandbox."""
    if not os.environ.get("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH"):
        monkeypatch.setenv(
            "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
            str(tmp_path / "management-nl-command-idempotency.json"),
        )


# ---------------------------------------------------------------------------
# 1 & 2: legacy mechanism deletion / durable store is the sole mechanism
# ---------------------------------------------------------------------------


def test_legacy_in_memory_idempotency_dict_no_longer_exists() -> None:
    # Checked against both the real seam module (management_service, where
    # the durable admission machinery now lives) and main.py's own source
    # text directly (no import needed -- AC3), so a leftover definition in
    # either place would still be caught.
    assert not hasattr(bff_main, "_MGMT_NL_IDEMPOTENCY")
    assert "_MGMT_NL_IDEMPOTENCY" not in MAIN_PY_PATH.read_text(encoding="utf-8")


def test_legacy_command_idempotency_required_flag_is_fully_deleted() -> None:
    assert not hasattr(bff_main, "_mgmt_nl_command_idempotency_required")

    main_source = MAIN_PY_PATH.read_text(encoding="utf-8")
    assert "_mgmt_nl_command_idempotency_required" not in main_source
    assert "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_REQUIRED" not in main_source

    compose_path = REPO_ROOT / "docker-compose.yml"
    compose_text = compose_path.read_text(encoding="utf-8")
    assert "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_REQUIRED" not in compose_text
    # The durable store's own configuration knobs must still be present.
    assert "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH" in compose_text
    assert "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_RECOVERY_SECONDS" in compose_text


def test_legacy_idempotency_check_and_put_helpers_are_deleted() -> None:
    assert not hasattr(bff_main, "_mgmt_nl_idempotency_check")
    assert not hasattr(bff_main, "_mgmt_nl_idempotency_put")
    main_source = MAIN_PY_PATH.read_text(encoding="utf-8")
    assert "def _mgmt_nl_idempotency_check" not in main_source
    assert "def _mgmt_nl_idempotency_put" not in main_source


# ---------------------------------------------------------------------------
# 3: single shared use-case entry point, both transports are async
# ---------------------------------------------------------------------------


def test_both_transports_are_coroutine_functions() -> None:
    assert inspect.iscoroutinefunction(bff_main.bff_management_nl_ask)
    assert inspect.iscoroutinefunction(bff_main.bff_management_nl_ask_stream)


def test_single_composed_use_case_instance_owns_admission_logic() -> None:
    assert isinstance(bff_main._MANAGEMENT_NL_USE_CASE, ManagementNlUseCase)


def test_ask_and_stream_both_call_the_same_shared_admission_functions() -> None:
    """Both handlers must call the identical module-level admission
    functions (which themselves delegate to the single composed
    ManagementNlUseCase) rather than each rolling their own admit/replay
    logic. Source-inspection proxy for "no duplicated admission logic
    between the two handlers".
    """
    # bff_management_nl_ask / bff_management_nl_ask_stream are thin
    # fail-closed wrappers (mark a held reservation uncertain on any
    # unhandled exception) around the real implementations, which are what
    # actually calls the shared admission entry points.
    ask_wrapper_source = inspect.getsource(bff_main.bff_management_nl_ask)
    stream_wrapper_source = inspect.getsource(bff_main.bff_management_nl_ask_stream)
    for wrapper_source in (ask_wrapper_source, stream_wrapper_source):
        assert "_MGMT_NL_COMMAND_RESERVATION_CONTEXT" in wrapper_source
        assert "_mgmt_nl_command_mark_uncertain(" in wrapper_source

    ask_source = inspect.getsource(bff_main._bff_management_nl_ask_impl)
    stream_source = inspect.getsource(bff_main._bff_management_nl_ask_stream_impl)

    for shared_call in ("_mgmt_nl_command_admit(", "_mgmt_nl_command_scope("):
        assert shared_call in ask_source, f"ask handler must call {shared_call}"
        assert shared_call in stream_source, f"stream handler must call {shared_call}"

    # Every module-level admission function is a thin delegator onto the one
    # composed use case -- not a second, parallel implementation.
    for fn_name in (
        "_mgmt_nl_command_admit",
        "_mgmt_nl_command_complete",
        "_mgmt_nl_command_mark_uncertain",
    ):
        fn_source = inspect.getsource(getattr(bff_main, fn_name))
        assert "_MANAGEMENT_NL_USE_CASE" in fn_source


def test_ask_and_stream_share_one_canonical_command_route() -> None:
    """ask and ask/stream are one durable use case with two transports: they
    must scope admission under the same canonical route name so a client
    can switch transports without losing dedup."""
    scope_a = bff_main._mgmt_nl_command_scope(
        actor_id="actor-1", tenant_id="tenant-1", resolved_key="shared-key"
    )
    assert scope_a.route == bff_main._MGMT_NL_COMMAND_ROUTE


# ---------------------------------------------------------------------------
# 4: behavioural — stream transport goes through real durable admission
# ---------------------------------------------------------------------------


def test_stream_replays_terminal_result_without_invoking_provider_twice(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        payload = {
            "question": "Single owner stream replay check",
            "focus": "portfolio",
            "sessionId": "single-owner-stream-replay",
        }
        headers = {**OPERATOR_HEADERS, "Idempotency-Key": "single-owner-stream-replay-key"}

        first = client.post("/bff/management/nl/ask/stream", json=payload, headers=headers)
        assert first.status_code == 200, first.text
        assert "Streamed provider answer." in first.text
        assert len(fake.calls) == 1

        second = client.post("/bff/management/nl/ask/stream", json=payload, headers=headers)
        assert second.status_code == 200, second.text
        assert "Streamed provider answer." in second.text
        # The durable replay path must not invoke the provider a second time.
        assert len(fake.calls) == 1
        assert '"replayed": true' in second.text
    finally:
        set_management_nl_read_store(original_store)
        clear_management_nl_sse_buffer("ask")


def test_stream_returns_409_on_same_key_different_payload(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        headers = {**OPERATOR_HEADERS, "Idempotency-Key": "single-owner-stream-conflict-key"}
        first = client.post(
            "/bff/management/nl/ask/stream",
            json={
                "question": "First payload owns this key",
                "focus": "portfolio",
                "sessionId": "single-owner-stream-conflict-a",
            },
            headers=headers,
        )
        assert first.status_code == 200, first.text

        second = client.post(
            "/bff/management/nl/ask/stream",
            json={
                "question": "Different payload must conflict",
                "focus": "portfolio",
                "sessionId": "single-owner-stream-conflict-b",
            },
            headers=headers,
        )
        assert second.status_code == 409, second.text
        body = second.json()
        assert body["error"]["details"]["precondition_failed"] == "idempotency_conflict"
        assert len(fake.calls) == 1
    finally:
        set_management_nl_read_store(original_store)
        clear_management_nl_sse_buffer("ask")


def test_stream_cross_tenant_requests_do_not_share_a_replay(tmp_path, monkeypatch) -> None:
    original_store = get_management_nl_read_store()
    fake = FakeProviderClient()
    try:
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED", "true")
        monkeypatch.setattr(bff_main, "OpenClawOpsClient", lambda: fake)
        client = _seeded_client(tmp_path, monkeypatch)

        payload = {
            "question": "Cross tenant isolation check",
            "focus": "portfolio",
            "sessionId": "single-owner-stream-tenant-a",
        }
        key = "single-owner-stream-tenant-key"

        first = client.post(
            "/bff/management/nl/ask/stream",
            json=payload,
            headers={**OPERATOR_HEADERS, "Idempotency-Key": key, "X-Tenant-Id": "tenant-alpha"},
        )
        assert first.status_code == 200, first.text
        assert len(fake.calls) == 1

        second = client.post(
            "/bff/management/nl/ask/stream",
            json={**payload, "sessionId": "single-owner-stream-tenant-b"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": key, "X-Tenant-Id": "tenant-beta"},
        )
        assert second.status_code == 200, second.text
        # A different tenant scope must invoke the provider again -- the
        # durable command scope is actor+tenant+route+key, not just key.
        assert len(fake.calls) == 2
    finally:
        set_management_nl_read_store(original_store)
        clear_management_nl_sse_buffer("ask")
