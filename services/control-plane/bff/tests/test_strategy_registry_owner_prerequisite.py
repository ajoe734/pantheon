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
from services.control_plane.bff.command_adapters.strategy_adapter import StrategyCommandAdapter


@pytest.fixture
def adapter():
    return StrategyCommandAdapter()


@pytest.fixture(autouse=True)
def registry_url_env(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry-svc.internal")


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_preserves_callers_precondition_and_uses_patch_response_as_truth(mock_http, adapter):
    """The caller's own expected_metadata (its real CAS precondition) must
    reach the PATCH unchanged — never silently refreshed to "latest" via an
    extra GET first — and the receipt must reflect the actual PATCH response,
    not a separate re-GET."""
    mock_http.return_value = (
        200,
        {"X-Idempotent-Replay": "false"},
        {
            "entry": {
                "registry_id": "reg-001",
                "strategy_id": "strat-alpha",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T00:00:00Z",
                "checksum": "sha256:abc",
            }
        },
    )

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
        auth_token="test-token",
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
    assert readback_call.args[0] == "http://registry-svc.internal/api/registry/entries/reg-001"
    assert patch_call.kwargs["payload"]["metadata"] == {"note": "new"}
    assert patch_call.kwargs["payload"]["command_key"] == "cmd-strat-001"


@patch("services.control_plane.bff.command_adapters.strategy_adapter.http_request_json_with_headers")
def test_update_params_idempotent_replay_header_is_surfaced(mock_http, adapter):
    mock_http.return_value = (
        200,
        {"X-Idempotent-Replay": "true"},
        {
            "entry": {
                "registry_id": "reg-001",
                "strategy_id": "strat-alpha",
                "metadata": {"note": "v1"},
                "updated_at": "t1",
                "last_actor": {"actor_id": "operator-a", "tenant": "tenant-a"},
            }
        },
    )

    # mock_http.return_value applies identically to every call this makes
    # (pre-check GET + PATCH); both see the same strat-alpha entry shape.
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
    )
    assert result["status"] == "metadata_updated"
    assert result["idempotent_replay"] is True
    # A replay must not issue a *third* (post-PATCH readback) HTTP call —
    # there is nothing to verify against that could ever be more
    # authoritative than the original committed receipt itself. Only the
    # pre-mutation identity-verification GET and the PATCH itself run.
    assert mock_http.call_count == 2


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
        )
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
        (200, {}, {"entry": wrong_scope_readback}),
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
        payload = json.dumps(type(self).response_body).encode("utf-8")
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
        server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", f"http://127.0.0.1:{server.server_port}")
        yield
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_callers_base_precondition_is_sent_unchanged_over_the_wire(self, adapter):
        _CapturingHandler.response_body = {
            "entry": {
                "registry_id": "reg-001",
                "strategy_id": "strat-alpha",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T00:00:00Z",
                "checksum": "sha256:real",
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
        _CapturingHandler.response_body = {
            "entry": {
                "registry_id": "reg-001",
                "strategy_id": "strat-alpha",
                "metadata": {"note": "new"},
                "updated_at": "2026-09-06T01:23:45Z",
                "checksum": "sha256:exact-version",
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
        )
        assert result["status"] == "metadata_updated"
        assert result["domain_receipt"]["checksum"] == "sha256:exact-version"
        assert result["domain_receipt"]["commit_time"] == "2026-09-06T01:23:45Z"
        assert result["domain_receipt"]["correlation_id"] == "cmd-real-002"
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
            )
        assert excinfo.value.error_code == "REGISTRY_ID_NOT_FOUND"
        # The PATCH must never have been attempted.
        assert all(record["method"] == "GET" for record in _CapturingHandler.received_log)
