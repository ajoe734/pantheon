"""StrategyCommandAdapter must call the real Registry owner and verify a
durable readback, never fabricate a resulting_status.

Prior behavior (architecture-resumption-sa-sd.md §2): _execute_strategy_action
mapped action_id -> a static resulting_status and returned it as an
"authoritative_readback" with zero owner I/O. A later fix made update_params
perform real owner I/O but still had two defects (reviewer finding 6):
it silently replaced the caller's ``expected_metadata`` CAS precondition
with a freshly-fetched GET (defeating CAS), and it discarded the actual PATCH
response in favor of a separate re-GET that could return stale/unrelated/
empty data and still be reported as a "metadata_updated" success.

This suite proves the current, corrected contract:
- update_params performs exactly one HTTP call — a PATCH carrying the
  caller's own ``expected_metadata`` unchanged — and builds its receipt from
  that PATCH response (entry snapshot + ``X-Idempotent-Replay`` header), not
  from a second GET.
- A PATCH response with no confirmable entry payload raises explicitly
  instead of manufacturing a false success.
- submit_review/promote_paper/activate/pause/archive raise
  ActionUnavailableError naming the exact non-Registry owner from
  services.registry.command_contract, instead of returning a fabricated
  status — a caller can no longer mistake "the adapter didn't crash" for
  "the business action happened".
"""
from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

from services.control_plane.bff.command_adapters.base import (
    ActionUnavailableError,
    http_request_json,
)
import time
from services.control_plane.bff.command_adapters.strategy_adapter import (
    StrategyCommandAdapter,
    _receipt_correlation_id,
)
from services.runtime_auth_inbound import encode_jwt_hs256

_TEST_JWT_SECRET = "test-bff-jwt-secret"
_TEST_JWT_ISSUER = "pantheon-bff-test"
_TEST_JWT_AUDIENCE = "pantheon-bff"


@pytest.fixture
def adapter():
    return StrategyCommandAdapter()


@pytest.fixture(autouse=True)
def bff_auth_env(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry-svc.internal")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", _TEST_JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", _TEST_JWT_AUDIENCE)


def _strict_token(
    *,
    sub: str = "operator-a",
    tenant: str = "tenant-a",
    role: str = "operator",
    exp_offset: float = 3600,
) -> str:
    return encode_jwt_hs256(
        {
            "sub": sub,
            "tenant": tenant,
            "roles": [role],
            "iss": _TEST_JWT_ISSUER,
            "aud": _TEST_JWT_AUDIENCE,
            "exp": time.time() + exp_offset,
        },
        secret=_TEST_JWT_SECRET,
    )


def _make_receipt(
    command_id: str,
    registry_id: str,
    entry: dict,
    *,
    actor_id: str = "test-token",
    tenant: str = "tenant-a",
    expected_metadata: Optional[dict] = None,
    new_metadata: Optional[dict] = None,
    committed_at: Optional[str] = None,
    receipt_key: Optional[str] = None,
    request_digest: Optional[str] = None,
) -> dict:
    from services.registry.pg_store import PostgresRegistryStore, _request_digest
    if receipt_key is None:
        receipt_key = PostgresRegistryStore.receipt_key(
            command_id, registry_id, actor={"actor_id": actor_id, "tenant": tenant}, command_type="metadata",
        )
    if request_digest is None:
        request_digest = _request_digest({
            "registry_id": registry_id,
            "expected_metadata": expected_metadata,
            "metadata": new_metadata if new_metadata is not None else entry.get("metadata"),
        })
    return {
        "command_key": command_id,
        "registry_id": registry_id,
        "receipt_key": receipt_key,
        "request_digest": request_digest,
        "committed_at": committed_at or entry.get("updated_at") or "2026-09-06T00:00:00Z",
        "committed_entry": entry,
    }


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_preserves_callers_precondition_and_uses_patch_response_as_truth(mock_http, adapter):
    """The caller's own expected_metadata (its real CAS precondition) must
    reach the PATCH unchanged — never silently refreshed to "latest" via an
    extra GET first — and the receipt must reflect the actual PATCH response,
    not a separate re-GET."""
    mock_http.side_effect = [
        (
            200,
            {},
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "old"},
                    "updated_at": "2026-09-05T00:00:00Z",
                    "checksum": "sha256:abc",
                }
            },
        ),
        (
            200,
            {"X-Idempotent-Replay": "false"},
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "new"},
                    "updated_at": "2026-09-06T00:00:00Z",
                    "checksum": "sha256:abc",
                    "last_actor": {"actor_id": "test-token", "tenant": "tenant-a"},
                }
            },
        ),
        (
            200,
            {},
            {
                "receipt": _make_receipt(
                    "cmd-strat-001",
                    "reg-001",
                    {
                        "registry_id": "reg-001",
                        "strategy_id": "strat-alpha",
                        "metadata": {"note": "new"},
                        "updated_at": "2026-09-06T00:00:00Z",
                        "checksum": "sha256:abc",
                        "last_actor": {"actor_id": "test-token", "tenant": "tenant-a"},
                    },
                    actor_id="test-token",
                    expected_metadata={"note": "old"},
                    new_metadata={"note": "new"},
                )
            },
        ),
    ]

    result = adapter.execute(
        "cmd-strat-001",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "expected_metadata": {"note": "old"},
            "metadata": {"note": "new"},
        },
        auth_token=_strict_token(sub="test-token"),
    )

    assert result["status"] == "metadata_updated"
    # The authoritative_readback is the entry from the PATCH response itself.
    assert result["authoritative_readback"]["metadata"] == {"note": "new"}
    assert result["entity_id"] == "strat-alpha"
    assert result["domain_receipt"]["registry_id"] == "reg-001"
    assert result["domain_receipt"]["checksum"] == "sha256:abc"
    assert result["idempotent_replay"] is False

    # Reviewer finding 6: three HTTP calls now — a pre-mutation owner GET
    # that verifies registry_id actually belongs to strategy_id and captures
    # the pre-issue immutable baseline (registry_id/checksum/version/
    # owner_tenant) BEFORE any mutating call, then the PATCH itself
    # (carrying the caller's own expected_metadata unchanged — no "refresh
    # to latest" substitution), then a genuine owner GET readback that
    # verifies what the PATCH response claimed (reviewer finding 5 — never
    # trust the mutation response alone as proof of what committed).
    assert mock_http.call_count == 3
    precheck_call = mock_http.call_args_list[0]
    assert precheck_call.kwargs["method"] == "GET"
    patch_call = mock_http.call_args_list[1]
    assert patch_call.kwargs["method"] == "PATCH"
    assert patch_call.kwargs["payload"]["expected_metadata"] == {"note": "old"}
    readback_call = mock_http.call_args_list[2]
    assert readback_call.kwargs["method"] == "GET"
    assert readback_call.args[0].startswith("http://registry-svc.internal/api/registry/entries/reg-001/receipts/cmd-strat-001")
    assert patch_call.kwargs["payload"]["metadata"] == {"note": "new"}
    assert patch_call.kwargs["payload"]["command_key"] == "cmd-strat-001"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_idempotent_replay_header_is_surfaced(mock_http, adapter):
    entry_snapshot = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "metadata": {"note": "v1"},
        "updated_at": "t1",
        "checksum": "sha256:abc",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    mock_http.side_effect = [
        (200, {}, {"entry": entry_snapshot}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": entry_snapshot}),
        (200, {}, {"receipt": _make_receipt("cmd-strat-005", "reg-001", entry_snapshot, actor_id="operator-a", tenant="tenant-a", expected_metadata=None, new_metadata={"note": "v1"}, committed_at="t1")}),
    ]

    result = adapter.execute(
        "cmd-strat-005",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "expected_metadata": None,
            "metadata": {"note": "v1"},
        },
        auth_token=_strict_token(),
    )
    assert result["idempotent_replay"] is True


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_rejects_registry_id_belonging_to_different_strategy(mock_http, adapter):
    """Reviewer finding 6: registry_id is caller-supplied and must actually
    belong to the requested strategy_id. A caller targeting strategy A but
    supplying a registry_id that belongs to strategy B must not get back a
    receipt claiming A was updated."""
    mock_http.return_value = (
        200,
        {"X-Idempotent-Replay": "false"},
        {
            "entry": {
                "registry_id": "reg-001",
                "strategy_id": "strat-bravo",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T00:00:00Z",
                "checksum": "sha256:abc",
            }
        },
    )

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-cross",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "STRATEGY_ID_MISMATCH"
    # Reviewer finding 6: the mismatch must be caught by the pre-mutation
    # identity-verification GET — the mutating PATCH must never be issued
    # against the wrong aggregate in the first place.
    assert mock_http.call_count == 1
    assert mock_http.call_args_list[0].kwargs["method"] == "GET"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_replay_with_forged_registry_id_is_rejected_despite_matching_strategy_id(
    mock_http, adapter,
):
    """Reviewer finding 7: a replay response reporting the right strategy_id
    but a wrong registry_id/checksum (a fault-injected or forged replay
    body) must be rejected — matching strategy_id alone is not sufficient
    proof this is a genuine replay of the exact command that was issued."""
    call_responses = [
        # Pre-mutation identity-verification GET: the real reg-001.
        (
            200,
            {},
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "old"},
                    "checksum": "sha256:real",
                }
            },
        ),
        # The PATCH "replay" response claims strategy_id matches but reports
        # a different registry_id and checksum entirely.
        (
            200,
            {"X-Idempotent-Replay": "true"},
            {
                "entry": {
                    "registry_id": "reg-999-wrong",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "forged"},
                    "checksum": "sha256:forged",
                }
            },
        ),
    ]
    mock_http.side_effect = call_responses

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-forged-replay",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": None,
                "metadata": {"note": "one"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "READBACK_MISMATCH"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_replay_does_not_compare_against_mutable_latest_readback(mock_http, adapter):
    """Reviewer finding 7: a replay's PATCH response is the original
    committed snapshot, not a claim to be re-verified against a "current"
    owner GET — comparing against a readback that a later, unrelated command
    has since moved on must not turn a genuine replay success into a
    spurious READBACK_MISMATCH failure."""
    entry_snapshot = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "metadata": {"note": "one"},
        "updated_at": "t1",
        "checksum": "sha256:one",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    call_responses = [
        # Pre-mutation owner GET (reviewer finding 6) — captures the
        # immutable baseline this call's replay verification is bound to.
        (200, {}, {"entry": entry_snapshot}),
        # The PATCH itself, reporting a replay.
        (200, {"X-Idempotent-Replay": "true"}, {"entry": entry_snapshot}),
        # Reviewer finding (9a6c review, P1): independent durable receipt reload.
        (
            200,
            {},
            {
                "receipt": _make_receipt(
                    "cmd-strat-replay",
                    "reg-001",
                    entry_snapshot,
                    actor_id="operator-a",
                    tenant="tenant-a",
                    expected_metadata=None,
                    new_metadata={"note": "one"},
                    committed_at="t1",
                )
            },
        ),
    ]
    mock_http.side_effect = call_responses

    result = adapter.execute(
        "cmd-strat-replay",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "expected_metadata": None,
            "metadata": {"note": "one"},
        },
        auth_token=_strict_token(),
    )
    assert result["status"] == "metadata_updated"
    assert result["idempotent_replay"] is True
    assert mock_http.call_count == 3


# ===========================================================================
# Gen-8 independent Codex rejection of PR #5620 (findings 3-4): the owner
# GET/PATCH responses above were compared for *internal consistency*
# (readback matches the PATCH response) but never against what the caller
# actually requested, and a replay's own claimed content was trusted with no
# sanity check beyond the immutable identity fields that never change on a
# metadata-only commit in the first place.
# ===========================================================================


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_readback_confirming_unapplied_metadata_is_rejected(mock_http, adapter):
    """Reviewer finding 3: if the owner silently no-ops the write (the PATCH
    response and the follow-up readback both report the *old*, unchanged
    metadata), every prior check (self-consistency between PATCH response
    and readback, scope/identity match) passed — but the requested update was
    never actually applied. This must be rejected, not reported as
    metadata_updated."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),  # pre-mutation identity GET
        (200, {"X-Idempotent-Replay": "false"}, {"entry": baseline}),  # PATCH: unchanged
        (200, {}, {"entry": baseline}),  # follow-up readback: also unchanged
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-unapplied",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    # A PATCH response whose commit timestamp is unchanged from the
    # pre-mutation baseline is now caught explicitly and earlier (before the
    # follow-up readback ever needs to run) — see COMMIT_TIME_UNCHANGED.
    assert excinfo.value.error_code == "READBACK_MISMATCH"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_readback_for_wrong_scope_is_rejected(mock_http, adapter):
    """Reviewer finding 3: a follow-up readback that reports a *different*
    strategy_id/owner_tenant/version than the command targeted must be
    rejected even if its checksum/updated_at/metadata happen to match the
    PATCH response — those three fields alone are not proof it is the same
    aggregate."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    wrong_scope_readback = dict(patched, strategy_id="strat-other", owner_tenant="tenant-other", version="9.0.0")
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": patched}),
        (
            200,
            {},
            {
                "receipt": _make_receipt(
                    "cmd-wrong-scope",
                    "reg-001",
                    wrong_scope_readback,
                    actor_id="operator-a",
                    tenant="tenant-a",
                    expected_metadata={"note": "old"},
                    new_metadata={"note": "new"},
                )
            },
        ),
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-wrong-scope",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "READBACK_MISMATCH"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_replay_with_unrelated_metadata_is_rejected(mock_http, adapter):
    """Reviewer finding 4: a replay response reporting metadata that differs
    from what this exact command requested must be rejected — matching
    registry_id/checksum/version/owner_tenant is not sufficient on its own
    for a metadata commit, since those fields are exactly what stays fixed
    while metadata is what changed."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    forged_replay = dict(
        baseline,
        metadata={"note": "unrelated"},
        last_actor={"actor_id": "other-actor", "tenant": "tenant-a"},
        updated_at=None,
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": forged_replay}),
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-replay-unrelated",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "REPLAY_METADATA_MISMATCH"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_replay_missing_commit_time_is_rejected(mock_http, adapter):
    """Reviewer finding 4: a replay response with the requested metadata but
    no commit timestamp is not a trustworthy original receipt."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    replay_missing_time = dict(baseline, metadata={"note": "new"}, updated_at=None)
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": replay_missing_time}),
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-replay-no-time",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "REPLAY_MISSING_COMMIT_TIME"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_replay_missing_actor_is_rejected(mock_http, adapter):
    """Reviewer finding 4: a replay response with the requested metadata and
    a commit timestamp but no recorded actor is not a trustworthy original
    receipt either."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    replay_missing_actor = dict(
        baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z", last_actor=None,
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": replay_missing_actor}),
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-replay-no-actor",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "REPLAY_MISSING_ACTOR"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_readback_failure_after_confirmed_commit_is_retryable(mock_http, adapter):
    """Reviewer finding 7: when the PATCH itself already returned a concrete
    committed entry snapshot, a subsequent readback GET failure is a
    transient confirmation gap — not proof the mutation never happened — and
    must be reported as retryable, not the flat non-retryable 422 used for a
    genuinely unsupported action."""

    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        if kwargs.get("method") == "PATCH":
            return (
                200,
                {"X-Idempotent-Replay": "false"},
                {
                    "entry": {
                        "registry_id": "reg-001",
                        "strategy_id": "strat-alpha",
                        "metadata": {"note": "new"},
                        "updated_at": "2026-09-06T00:00:00Z",
                        "checksum": "sha256:abc",
                        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
                    }
                },
            )
        if call_count["n"] == 1:
            # The pre-mutation identity-verification GET (reviewer finding
            # 6) must succeed so the PATCH is actually attempted; only the
            # *post-PATCH* readback GET is unavailable here.
            return (
                200,
                {},
                {
                    "entry": {
                        "registry_id": "reg-001",
                        "strategy_id": "strat-alpha",
                        "metadata": {"note": "old"},
                        "updated_at": "2026-09-05T00:00:00Z",
                        "checksum": "sha256:abc",
                    }
                },
            )
        raise urllib.error.URLError("readback unavailable")

    mock_http.side_effect = _side_effect

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-readback-gap",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "READBACK_UNAVAILABLE"
    assert excinfo.value.retryable is True
    assert excinfo.value.downstream_status == 503


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_ambiguous_patch_response_never_fabricates_success(mock_http, adapter):
    """A 200 response with no confirmable entry payload (e.g. an empty body)
    must never be reported as metadata_updated — that would be exactly the
    "wrong-version/empty GET manufactures a fake success" defect."""
    call_responses = [
        # Pre-mutation identity-verification GET (reviewer finding 6) must
        # succeed so the PATCH itself is attempted and its own ambiguous
        # response is what's under test here.
        (
            200,
            {},
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "old"},
                    "checksum": "sha256:abc",
                }
            },
        ),
        # The PATCH itself returns an ambiguous/empty body.
        (200, {}, {}),
        # The follow-up readback attempt (distinguishing "committed but
        # response lost" from "not committed") also comes back empty.
        (200, {}, {}),
    ]
    mock_http.side_effect = call_responses

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-006",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": None,
                "metadata": {"note": "v1"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "AMBIGUOUS_REGISTRY_RESPONSE"


# ===========================================================================
# Gen-10 independent Codex rejection of PR #5620 (finding 3): the checks
# above verified internal self-consistency and identity/content matching,
# but still trusted a single response body's own self-reported actor/
# timestamp claims in three additional ways a fault-injection probe
# reproduced live: (a) a replay reporting *some* actor_id, even one that
# could not legitimately be this caller's own prior command; (b) a normal
# (non-replay) response with a null commit timestamp and no actor, whose
# self-consistency with an equally-null follow-up readback passed every
# existing check; (c) an ambiguous/empty PATCH body whose follow-up readback
# happened to already match the target metadata *before* this command ever
# ran, with no signal distinguishing "this command committed it" from "it
# was already there".
# ===========================================================================


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_replay_actor_not_matching_caller_token_is_rejected(mock_http, adapter):
    """Reviewer finding 3 (gen-10 review): a replay response reporting an
    actor_id that does not match the verified caller's own token-derived
    identity is not trustworthy proof of this caller's own prior command,
    even though it carries a non-empty actor_id, a commit timestamp, and the
    requested metadata."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    replay_wrong_actor = dict(
        baseline,
        metadata={"note": "new"},
        updated_at="2026-09-06T01:00:01Z",
        last_actor={"actor_id": "other-actor", "tenant": "tenant-a"},
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": replay_wrong_actor}),
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-replay-wrong-actor",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(sub="caller-actor-id"),
        )
    assert excinfo.value.error_code == "REPLAY_ACTOR_MISMATCH"
    # No third HTTP call — the verification is local, derived from the
    # caller's own auth_token, not an extra network round trip.
    assert mock_http.call_count == 2


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_replay_actor_matching_caller_token_still_succeeds(mock_http, adapter):
    """A genuine replay whose recorded actor matches the verified caller's
    own token-derived identity must still succeed — the new check is a
    cross-check against forgery, not a new general restriction."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "caller-actor-id", "tenant": "tenant-a"},
    }
    replay_same_actor = dict(
        baseline,
        metadata={"note": "new"},
        updated_at="2026-09-06T01:00:01Z",
        last_actor={"actor_id": "caller-actor-id", "tenant": "tenant-a"},
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": replay_same_actor}),
        (
            200,
            {},
            {
                "receipt": _make_receipt(
                    "cmd-replay-same-actor",
                    "reg-001",
                    replay_same_actor,
                    actor_id="caller-actor-id",
                    expected_metadata={"note": "old"},
                    new_metadata={"note": "new"},
                    committed_at="2026-09-06T01:00:01Z",
                )
            },
        ),
    ]

    result = adapter.execute(
        "cmd-replay-same-actor",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "expected_metadata": {"note": "old"},
            "metadata": {"note": "new"},
        },
        auth_token=_strict_token(sub="caller-actor-id"),
    )
    assert result["status"] == "metadata_updated"
    assert result["idempotent_replay"] is True


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_normal_response_missing_commit_time_is_rejected(mock_http, adapter):
    """Reviewer finding 3 (gen-10 review): a normal (non-replay) PATCH
    response with a null commit timestamp is not trustworthy proof of
    commit, even when a follow-up readback is internally self-consistent
    with it (both null) and reports the requested metadata."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    no_time_no_actor = dict(baseline, metadata={"note": "new"}, updated_at=None, last_actor=None)
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": no_time_no_actor}),
        (200, {}, {"entry": no_time_no_actor}),
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-normal-no-time",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "MISSING_COMMIT_TIME"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_ambiguous_response_readback_matching_before_the_command_ran_is_rejected(
    mock_http, adapter,
):
    """Reviewer finding 3 (gen-10 review): an ambiguous/empty PATCH body
    recovered via a follow-up readback that confirms the target metadata is
    not proof *this* command committed it if that same metadata/timestamp
    was already present in the pre-mutation identity-verification GET
    captured before the command ever ran — the entry may simply have already
    been at that value (e.g. the CAS was actually rejected)."""
    already_at_target = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:original",
        "metadata": {"note": "new"},
        "updated_at": "2026-09-06T01:00:01Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    mock_http.side_effect = [
        (200, {}, {"entry": already_at_target}),  # pre-mutation identity GET
        (200, {}, {}),  # ambiguous PATCH response
        (200, {}, {"entry": already_at_target}),  # follow-up readback: unchanged from baseline
    ]

    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-ambiguous-no-commit",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "AMBIGUOUS_REGISTRY_RESPONSE"


def test_update_params_requires_registry_id(adapter):
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-002",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": "update_params",
                "expected_metadata": None,
                "metadata": {"note": "new"},
            },
        )
    assert excinfo.value.error_code == "MISSING_REGISTRY_ID"


def test_update_params_requires_expected_metadata(adapter):
    """Omitting expected_metadata entirely must fail explicitly rather than
    the adapter silently treating it as None or fetching a fresh base."""
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-007",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "metadata": {"note": "new"},
            },
        )
    assert excinfo.value.error_code == "MISSING_EXPECTED_METADATA"


@pytest.mark.parametrize(
    "action_id,expected_owner",
    [
        ("submit_review", "governance_review"),
        ("promote_paper", "promotion"),
        ("activate", "runtime"),
        ("pause", "runtime"),
        ("archive", "runtime"),
    ],
)
def test_non_registry_actions_fail_explicitly_naming_the_real_owner(adapter, action_id, expected_owner):
    """These must never return a fabricated resulting_status again."""
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-strat-003",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": action_id,
            },
        )
    assert expected_owner in str(excinfo.value)
    assert excinfo.value.error_code == "OWNER_NOT_INTEGRATED"


def test_unrecognized_action_fails_explicitly(adapter):
    with pytest.raises(ActionUnavailableError):
        adapter.execute(
            "cmd-strat-004",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "action_id": "not_a_real_action",
            },
        )


class _CapturingHandler(BaseHTTPRequestHandler):
    """Records the received method/headers/body and replays a canned response.

    Configured per-test via class attributes so each request against a real
    socket proves what actually went out on the wire, not a mocked stand-in.
    """

    response_status = 200
    response_body: Dict[str, Any] = {"ok": True}
    # When set, overrides response_body for the PATCH request specifically —
    # lets a test give the mutating PATCH a distinct (advanced) updated_at/
    # last_actor from the pre-mutation and readback GETs, which otherwise
    # all share the same static response_body.
    patch_response_body: Optional[Dict[str, Any]] = None
    received: Optional[Dict[str, Any]] = None
    received_log: list = []

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "headers": dict(self.headers.items()),
            "body": json.loads(raw_body.decode("utf-8")) if raw_body else None,
        }
        type(self).received = record
        type(self).received_log = type(self).received_log + [record]
        if self.command == "PATCH" and type(self).patch_response_body is not None:
            body = type(self).patch_response_body
        else:
            body = type(self).response_body
            if isinstance(body, list):
                # response_body as a list lets a test give the pre-mutation
                # identity GET and the post-PATCH readback GET distinct
                # snapshots (e.g. an advancing updated_at), since both are
                # plain GETs and would otherwise share one static body.
                get_index = sum(1 for r in type(self).received_log if r["method"] != "PATCH") - 1
                body = body[min(get_index, len(body) - 1)]
        if "/receipts/" in self.path and isinstance(body, dict) and "entry" in body and "receipt" not in body:
            cmd_key = self.path.split("?")[0].rstrip("/").split("/")[-1]
            entry_dict = body["entry"]
            patch_records = [r for r in type(self).received_log if r["method"] == "PATCH"]
            patch_body = patch_records[-1]["body"] if patch_records else {}
            body = {
                "receipt": _make_receipt(
                    cmd_key,
                    entry_dict.get("registry_id", "reg-001"),
                    entry_dict,
                    actor_id=entry_dict.get("last_actor", {}).get("actor_id", "operator-a"),
                    tenant="tenant-a",
                    expected_metadata=patch_body.get("expected_metadata"),
                    new_metadata=patch_body.get("metadata", entry_dict.get("metadata")),
                    committed_at=entry_dict.get("updated_at"),
                )
            }
        payload = json.dumps(body).encode("utf-8")
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        self._handle()

    def do_POST(self):  # noqa: N802
        self._handle()

    def do_PATCH(self):  # noqa: N802
        self._handle()

    def do_DELETE(self):  # noqa: N802
        self._handle()

    def log_message(self, format, *args):  # noqa: A002 - silence test server logging
        return


class TestHttpRequestJsonMethodDispatch:
    """Real-HTTP regressions for command_adapters.base.http_request_json.

    Before the REGISTRY-STRATEGY-UNIFIED-CONTRACT-001 fix, any non-GET method
    (including PATCH, already used by capital_adapter.py before Strategy
    needed it) fell through to command_executor._post_json, which hardcodes
    method="POST" -- so a PATCH request against a route that only accepts
    PATCH silently went out as POST. These tests exercise a real socket
    instead of a mocked http_request_json/_post_json/_get_json call, so a
    regression of the method-dispatch fix shows up as a real wrong-method
    request rather than a satisfied mock expectation.
    """

    @pytest.fixture(autouse=True)
    def _server(self):
        _CapturingHandler.received = None
        _CapturingHandler.received_log = []
        _CapturingHandler.response_status = 200
        _CapturingHandler.response_body = {"ok": True}
        _CapturingHandler.patch_response_body = None
        server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.port = server.server_port
        yield
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def _url(self, path: str = "/api/registry/entries/reg-001/metadata") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_get_sends_real_get(self):
        body = http_request_json(self._url(), method="GET")
        assert body == {"ok": True}
        assert _CapturingHandler.received["method"] == "GET"

    def test_post_sends_real_post_with_payload(self):
        body = http_request_json(self._url(), method="POST", payload={"a": 1})
        assert body == {"ok": True}
        assert _CapturingHandler.received["method"] == "POST"
        assert _CapturingHandler.received["body"] == {"a": 1}

    def test_patch_sends_real_patch_not_post(self):
        """The exact regression: PATCH must not be silently sent as POST."""
        body = http_request_json(self._url(), method="PATCH", payload={"metadata": {"note": "x"}})
        assert body == {"ok": True}
        assert _CapturingHandler.received["method"] == "PATCH"
        assert _CapturingHandler.received["body"] == {"metadata": {"note": "x"}}

    def test_delete_sends_real_delete(self):
        http_request_json(self._url(), method="DELETE")
        assert _CapturingHandler.received["method"] == "DELETE"

    def test_auth_and_mfa_tokens_are_forwarded_as_headers(self):
        http_request_json(self._url(), method="PATCH", payload={}, auth_token="tok-123", mfa_token="mfa-456")
        headers = _CapturingHandler.received["headers"]
        assert headers.get("Authorization") == "Bearer tok-123"
        assert headers.get("X-Mfa-Token") == "mfa-456"

    def test_bearer_prefixed_auth_token_is_not_double_wrapped(self):
        http_request_json(self._url(), method="GET", auth_token="Bearer already-prefixed")
        headers = _CapturingHandler.received["headers"]
        assert headers.get("Authorization") == "Bearer already-prefixed"

    def test_owner_conflict_status_propagates_as_http_error(self):
        """A real 409 (CAS conflict) from the owner must raise, never be
        swallowed into a fabricated success body."""
        _CapturingHandler.response_status = 409
        _CapturingHandler.response_body = {"error": "conflict"}
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            http_request_json(self._url(), method="PATCH", payload={})
        assert excinfo.value.code == 409

    def test_owner_5xx_error_propagates_as_http_error(self):
        _CapturingHandler.response_status = 503
        _CapturingHandler.response_body = {"error": "unavailable"}
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            http_request_json(self._url(), method="GET")
        assert excinfo.value.code == 503

    def test_timeout_raises_when_owner_does_not_respond(self):
        """A method that falls through to the urllib fallback path (e.g.
        PATCH) must honor an explicit short timeout rather than hang."""
        import socket

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        stall_port = listener.getsockname()[1]

        def _accept_and_stall():
            try:
                conn, _ = listener.accept()
                threading.Event().wait(2)
                conn.close()
            except OSError:
                pass

        acceptor = threading.Thread(target=_accept_and_stall, daemon=True)
        acceptor.start()
        try:
            with pytest.raises(Exception):
                http_request_json(
                    f"http://127.0.0.1:{stall_port}/api/registry/entries/reg-001/metadata",
                    method="PATCH",
                    payload={},
                    timeout=1,
                )
        finally:
            listener.close()
            acceptor.join(timeout=5)


class TestUpdateParamsOverRealSocket:
    """End-to-end regression for update_params against a real HTTP server
    standing in for the Registry owner — no mocking of http_request_json* —
    so the CAS-precondition and receipt-fidelity fixes are proven against
    what actually goes out on (and comes back over) the wire, not a mock
    expectation that could silently drift from the real contract.
    """

    @pytest.fixture(autouse=True)
    def _server(self, monkeypatch):
        _CapturingHandler.received = None
        _CapturingHandler.received_log = []
        _CapturingHandler.response_status = 200
        _CapturingHandler.response_body = {"ok": True}
        _CapturingHandler.patch_response_body = None
        server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", f"http://127.0.0.1:{server.server_port}")
        yield
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_callers_base_precondition_is_sent_unchanged_over_the_wire(self, adapter):
        # response_body is a list: [pre-mutation identity GET, post-PATCH
        # readback GET] — each plain GET pulls the next entry in order, so
        # the pre-check can carry the old (pre-commit) snapshot while the
        # readback confirms the same advanced snapshot the PATCH itself
        # returns via patch_response_body.
        _CapturingHandler.response_body = [
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "old"},
                    "updated_at": "2026-09-05T00:00:00Z",
                    "checksum": "sha256:real",
                }
            },
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "new"},
                    "updated_at": "2026-09-06T00:00:00Z",
                    "checksum": "sha256:real",
                    "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
                }
            },
        ]
        _CapturingHandler.patch_response_body = {
            "entry": {
                "registry_id": "reg-001",
                "strategy_id": "strat-alpha",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T00:00:00Z",
                "checksum": "sha256:real",
                "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
            }
        }
        adapter.execute(
            "cmd-real-001",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "callers-own-base"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
        # The adapter now performs a pre-mutation identity-verification GET
        # (reviewer finding 6), then the PATCH, then a genuine owner GET
        # readback (reviewer finding 5); ``received`` reflects the *last*
        # request (the readback GET), so assert the PATCH specifically
        # against the second entry in the request log.
        assert _CapturingHandler.received_log[0]["method"] == "GET"
        assert _CapturingHandler.received_log[1]["method"] == "PATCH"
        assert _CapturingHandler.received_log[1]["body"]["expected_metadata"] == {"note": "callers-own-base"}
        assert _CapturingHandler.received["method"] == "GET"

    def test_correct_patch_result_produces_receipt_bound_to_that_exact_response(self, adapter):
        _CapturingHandler.response_body = [
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "old"},
                    "updated_at": "2026-09-06T00:00:00Z",
                    "checksum": "sha256:exact-version",
                }
            },
            {
                "entry": {
                    "registry_id": "reg-001",
                    "strategy_id": "strat-alpha",
                    "metadata": {"note": "new"},
                    "updated_at": "2026-09-06T01:23:45Z",
                    "checksum": "sha256:exact-version",
                    "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
                }
            },
        ]
        _CapturingHandler.patch_response_body = {
            "entry": {
                "registry_id": "reg-001",
                "strategy_id": "strat-alpha",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T01:23:45Z",
                "checksum": "sha256:exact-version",
                "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
            }
        }
        result = adapter.execute(
            "cmd-real-002",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": None,
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
        assert result["status"] == "metadata_updated"
        assert result["domain_receipt"]["checksum"] == "sha256:exact-version"
        assert result["domain_receipt"]["commit_time"] == "2026-09-06T01:23:45Z"
        # correlation_id is now derived from the independently-verified
        # durable receipt (registry_id/version/checksum/commit_time/actor),
        # not the caller-supplied command_id alone — see
        # _receipt_correlation_id.
        assert result["domain_receipt"]["correlation_id"] == _receipt_correlation_id(
            registry_id="reg-001",
            version=None,
            checksum="sha256:exact-version",
            commit_time="2026-09-06T01:23:45Z",
            actor_id="operator-a",
            command_id="cmd-real-002",
        )
        assert result["authoritative_readback"]["updated_at"] == "2026-09-06T01:23:45Z"

    def test_empty_response_body_cannot_manufacture_a_fake_success(self, adapter):
        """A 200 with no entry payload (e.g. an unrelated/empty GET-shaped
        body) must not be reported as metadata_updated. Reviewer finding 6:
        the pre-mutation identity-verification GET now runs first and fails
        closed even earlier than before — the mutating PATCH is never even
        attempted when the registry_id cannot be resolved up front."""
        _CapturingHandler.response_body = {}
        with pytest.raises(ActionUnavailableError) as excinfo:
            adapter.execute(
                "cmd-real-003",
                "StrategyAction",
                {
                    "entity_type": "strategy",
                    "strategy_id": "strat-alpha",
                    "registry_id": "reg-001",
                    "action_id": "update_params",
                    "expected_metadata": None,
                    "metadata": {"note": "new"},
                },
                auth_token=_strict_token(),
            )
        assert excinfo.value.error_code == "REGISTRY_ID_NOT_FOUND"
        # The PATCH must never have been attempted.
        assert all(record["method"] == "GET" for record in _CapturingHandler.received_log)


# ===========================================================================
# Finding 1 regression tests (PR #5620 reopening findings):
# (a) a normal PATCH and GET with nonempty timestamp but last_actor=None;
# (b) a normal PATCH and GET returning exactly the preexisting metadata/timestamp, with no mutation;
# (c) an empty PATCH body followed by a changed row committed by other-actor;
# (d) a replay body with correct actor and entry fields but no owner command identity or independent original-receipt reload.
# ===========================================================================


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_regression_normal_patch_with_nonempty_time_but_missing_actor_is_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched_no_actor = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z", last_actor=None)
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": patched_no_actor}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-missing-actor",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "MISSING_ACTOR"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_regression_normal_patch_returning_preexisting_metadata_and_time_is_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": baseline}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-no-commit",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "READBACK_MISMATCH"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_regression_ambiguous_patch_followed_by_other_actor_commit_is_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    other_actor_receipt = {
        "command_key": "cmd-other",
        "registry_id": "reg-001",
        "committed_entry": {
            "registry_id": "reg-001",
            "strategy_id": "strat-alpha",
            "owner_tenant": "tenant-a",
            "version": "1.0.0",
            "checksum": "sha256:abc",
            "metadata": {"note": "new"},
            "updated_at": "2026-09-06T01:00:01Z",
            "last_actor": {"actor_id": "other-actor", "tenant": "tenant-a"},
        },
        "committed_at": "2026-09-06T01:00:01Z",
    }
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {}, {}),  # ambiguous empty PATCH response
        (200, {}, {"receipt": other_actor_receipt}),  # receipt was by other actor
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-ambiguous-other",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "AMBIGUOUS_REGISTRY_RESPONSE"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_regression_replay_without_owner_receipt_reload_is_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    replay_body = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": replay_body}),
        (404, {}, {"detail": "No committed command receipt found"}),  # receipt reload fails
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-replay-no-receipt",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "UNCONFIRMED_COMMAND_RECEIPT"


# ===========================================================================
# PR #5620 Reopening Finding 1 & 2 Adversarial Tests:
# - Strict scoped receipt validation:
#   * entry-only response rejection (no fabrication)
#   * OTHER-COMMAND rejection
#   * OTHER-REGISTRY rejection
#   * WRONG-KEY (scoped receipt_key) rejection
#   * WRONG-DIGEST rejection
#   * null committed_at rejection
#   * caller authentication via runtime_auth_inbound (missing & unverified tokens)
# - Ambiguous transport exception handling:
#   * RemoteDisconnected during PATCH recovers via receipt readback
#   * RemoteDisconnected during PATCH fails closed with retryable 503 UNCONFIRMED_COMMAND_RECEIPT
# ===========================================================================


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_entry_only_readback_rejected_as_unconfirmed(mock_http, adapter):
    """Entry-only response on receipt endpoint must never be treated as valid receipt."""
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "true"}, {"entry": patched}),
        (200, {}, {"entry": patched}),  # Entry-only response, NOT a receipt!
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-entry-only",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "UNCONFIRMED_COMMAND_RECEIPT"
    assert excinfo.value.retryable is True
    assert excinfo.value.downstream_status == 503


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_receipt_other_command_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    receipt = _make_receipt(
        "OTHER-COMMAND",
        "reg-001",
        patched,
        actor_id="operator-a",
        expected_metadata={"note": "old"},
        new_metadata={"note": "new"},
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": patched}),
        (200, {}, {"receipt": receipt}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-target",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "UNCONFIRMED_COMMAND_RECEIPT"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_receipt_other_registry_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    receipt = _make_receipt(
        "cmd-target",
        "OTHER-REGISTRY",
        patched,
        actor_id="operator-a",
        expected_metadata={"note": "old"},
        new_metadata={"note": "new"},
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": patched}),
        (200, {}, {"receipt": receipt}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-target",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "STRATEGY_ID_MISMATCH"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_receipt_wrong_scope_key_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    receipt = _make_receipt(
        "cmd-target",
        "reg-001",
        patched,
        actor_id="operator-a",
        expected_metadata={"note": "old"},
        new_metadata={"note": "new"},
        receipt_key="forged-or-wrong-scope-key",
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": patched}),
        (200, {}, {"receipt": receipt}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-target",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "UNCONFIRMED_COMMAND_RECEIPT"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_receipt_wrong_digest_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    receipt = _make_receipt(
        "cmd-target",
        "reg-001",
        patched,
        actor_id="operator-a",
        expected_metadata={"note": "old"},
        new_metadata={"note": "new"},
        request_digest="sha256:forged-request-digest",
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": patched}),
        (200, {}, {"receipt": receipt}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-target",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "UNCONFIRMED_COMMAND_RECEIPT"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_receipt_null_committed_at_rejected(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    receipt = _make_receipt(
        "cmd-target",
        "reg-001",
        patched,
        actor_id="operator-a",
        expected_metadata={"note": "old"},
        new_metadata={"note": "new"},
        committed_at=None,
    )
    receipt["committed_at"] = None
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        (200, {"X-Idempotent-Replay": "false"}, {"entry": patched}),
        (200, {}, {"receipt": receipt}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-target",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "REPLAY_MISSING_COMMIT_TIME"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_missing_auth_token_rejected_401(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
    }
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-no-auth",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=None,
        )
    assert excinfo.value.error_code == "UNAUTHORIZED"
    assert excinfo.value.downstream_status == 401
    assert mock_http.call_count == 0


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_adversarial_unverified_jwt_rejected_401(mock_http, adapter, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_AUTH_REQUIRED", "true")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "test-secret-key-123")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_ISSUER", "test-issuer")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_AUDIENCE", "test-audience")
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
    }
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-bad-jwt",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token="ey-forged-invalid-jwt-signature",
        )
    assert excinfo.value.error_code == "UNAUTHORIZED"
    assert excinfo.value.downstream_status == 401
    assert mock_http.call_count == 0


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_transport_disconnect_during_patch_recovers_via_receipt_readback(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    patched = dict(baseline, metadata={"note": "new"}, updated_at="2026-09-06T01:00:01Z")
    valid_receipt = _make_receipt(
        "cmd-disconnect",
        "reg-001",
        patched,
        actor_id="operator-a",
        expected_metadata={"note": "old"},
        new_metadata={"note": "new"},
        committed_at="2026-09-06T01:00:01Z",
    )
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        urllib.error.URLError("RemoteDisconnected('Remote end closed connection without response')"),
        (200, {}, {"receipt": valid_receipt}),
    ]
    result = adapter.execute(
        "cmd-disconnect",
        "StrategyAction",
        {
            "entity_type": "strategy",
            "strategy_id": "strat-alpha",
            "registry_id": "reg-001",
            "action_id": "update_params",
            "expected_metadata": {"note": "old"},
            "metadata": {"note": "new"},
        },
        auth_token=_strict_token(),
    )
    assert result["status"] == "metadata_updated"
    assert result["domain_receipt"]["commit_time"] == "2026-09-06T01:00:01Z"
    assert result["response_lost"] is True


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_transport_disconnect_during_patch_fails_closed_when_unconfirmed(mock_http, adapter):
    baseline = {
        "registry_id": "reg-001",
        "strategy_id": "strat-alpha",
        "owner_tenant": "tenant-a",
        "version": "1.0.0",
        "checksum": "sha256:abc",
        "metadata": {"note": "old"},
        "updated_at": "2026-09-06T01:00:00Z",
        "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
    }
    mock_http.side_effect = [
        (200, {}, {"entry": baseline}),
        urllib.error.URLError("RemoteDisconnected('Remote end closed connection without response')"),
        (404, {}, {"detail": "No committed command receipt found"}),
    ]
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-disconnect-unconfirmed",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=_strict_token(),
        )
    assert excinfo.value.error_code == "UNCONFIRMED_COMMAND_RECEIPT"
    assert excinfo.value.retryable is True
    assert excinfo.value.downstream_status == 503


def test_strict_auth_rejects_missing_claims(adapter):
    claims = {
        "sub": "operator-a",
        "tenant": "tenant-a",
        "roles": ["operator"],
        "exp": time.time() + 3600,
        "iss": _TEST_JWT_ISSUER,
        "aud": _TEST_JWT_AUDIENCE,
    }
    for missing_claim in ["sub", "tenant", "roles", "exp"]:
        bad_payload = dict(claims)
        bad_payload.pop(missing_claim)
        token = encode_jwt_hs256(bad_payload, secret=_TEST_JWT_SECRET)
        with pytest.raises(ActionUnavailableError) as excinfo:
            adapter.execute(
                f"cmd-missing-{missing_claim}",
                "StrategyAction",
                {
                    "entity_type": "strategy",
                    "strategy_id": "strat-alpha",
                    "registry_id": "reg-001",
                    "action_id": "update_params",
                    "expected_metadata": {"note": "old"},
                    "metadata": {"note": "new"},
                },
                auth_token=token,
            )
        assert excinfo.value.error_code in ("UNAUTHORIZED", "FORBIDDEN")


def test_strict_auth_rejects_insufficient_role(adapter):
    bad_payload = {
        "sub": "viewer-a",
        "tenant": "tenant-a",
        "roles": ["viewer"],
        "exp": time.time() + 3600,
        "iss": _TEST_JWT_ISSUER,
        "aud": _TEST_JWT_AUDIENCE,
    }
    token = encode_jwt_hs256(bad_payload, secret=_TEST_JWT_SECRET)
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-viewer-role",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token=token,
        )
    assert excinfo.value.error_code == "FORBIDDEN"


def test_strict_auth_rejects_bare_token_without_config(adapter, monkeypatch):
    monkeypatch.delenv("PANTHEON_BFF_JWT_SECRET", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_JWT_ISSUER", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_JWT_AUDIENCE", raising=False)
    monkeypatch.delenv("PANTHEON_RUNTIME_JWT_SECRET", raising=False)
    monkeypatch.delenv("PANTHEON_RUNTIME_JWT_ISSUER", raising=False)
    monkeypatch.delenv("PANTHEON_RUNTIME_JWT_AUDIENCE", raising=False)
    with pytest.raises(ActionUnavailableError) as excinfo:
        adapter.execute(
            "cmd-bare-token",
            "StrategyAction",
            {
                "entity_type": "strategy",
                "strategy_id": "strat-alpha",
                "registry_id": "reg-001",
                "action_id": "update_params",
                "expected_metadata": {"note": "old"},
                "metadata": {"note": "new"},
            },
            auth_token="bare-unsigned-operator-token",
        )
    assert excinfo.value.error_code == "UNAUTHORIZED"
