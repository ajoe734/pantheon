from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest


SERVICE_DIR = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
AUTHORIZATION_TOKEN = "training-session-service-token"
APPROVAL_DECISION_REF = "approval-decision://persona-alpha/session-alpha/generation-4"
TENANT_ID = "tenant-alpha"


def _load_module():
    name = "training_session_persona_target_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / "persona_target.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Transport:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json_body": dict(json_body) if json_body is not None else None,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _scenario(module, *, generation: int = 4):
    candidate = {
        "persona_patch_ref": "persona-patch-alpha-v4",
        "parameters": {"risk.max_drawdown": 0.08},
    }
    control_state = {"risk.max_drawdown": 0.08, "risk.max_leverage": 1.25}
    candidate_digest = module.canonical_digest(candidate)
    control_digest = module.canonical_digest(control_state)
    persona = {
        "persona_id": "persona-alpha",
        "tenant_id": TENANT_ID,
        "status": "active",
        "generation": generation - 1,
        "authority_status": "authoritative",
        "controller_record_ref": "persona-controller://persona-alpha/generation-3",
        "recorded_at": "2026-07-15T13:54:00Z",
        "source": "persona-authority",
    }
    precondition_digest = module.canonical_digest(persona)
    proof = {
        "proof_ref": "trainer-eval-proof:session-alpha:eval-4",
        "status": "passed",
        "tenant_id": TENANT_ID,
        "dataset_digest": "sha256:" + "d" * 64,
        "candidate_binding": candidate,
        "candidate_digest": candidate_digest,
        "controls": control_state,
        "controls_digest": control_digest,
        "authority": {
            "policy": {"approval_decision_ref": APPROVAL_DECISION_REF},
        },
        "target_precondition": {
            "persona_id": "persona-alpha",
            "tenant_id": TENANT_ID,
            "expected_previous_generation": generation - 1,
            "target_generation": generation,
            "precondition_digest": precondition_digest,
            "controller_record_ref": persona["controller_record_ref"],
        },
    }
    proof_digest = module.canonical_digest(proof)
    proof["proof_digest"] = proof_digest
    proof["runtime_evidence"] = {"sequence": 9, "checksum": "e" * 64}
    approval = {
        "decision_id": "approval-alpha-4",
        "approval_decision_ref": APPROVAL_DECISION_REF,
        "decision_state": "decided",
        "decision": "approved",
        "persona_id": "persona-alpha",
        "tenant_id": TENANT_ID,
        "session_id": "session-alpha",
        "candidate_digest": candidate_digest,
        "proof_digest": proof_digest,
        "decided_at": "2026-07-15T13:55:00Z",
        "expires_at": "2026-07-15T14:30:00Z",
        "source": "governance-authority",
        "authority_status": "authoritative",
        "controller_record_ref": "governance-controller://approval-alpha-4",
        "recorded_at": "2026-07-15T13:56:00Z",
    }
    approval_digest = module.canonical_digest(approval)
    binding = {
        "persona_id": "persona-alpha",
        "tenant_id": TENANT_ID,
        "session_id": "session-alpha",
        "candidate_digest": candidate_digest,
        "control_digest": control_digest,
        "proof_digest": proof_digest,
        "approval_digest": approval_digest,
        "generation": generation,
    }
    committed = {
        "status": "committed",
        **binding,
        "authority_status": "authoritative",
        "controller_record_ref": "persona-controller://persona-alpha/generation-4",
        "recorded_at": "2026-07-15T13:57:00Z",
    }
    inputs = {
        "persona_id": "persona-alpha",
        "tenant_id": TENANT_ID,
        "session_id": "session-alpha",
        "candidate": candidate,
        "control_state": control_state,
        "evaluation_proof": proof,
        "generation": generation,
        "idempotency_key": "teach-session-alpha-generation-4",
        "expected_precondition_digest": precondition_digest,
        "approval_decision_ref": APPROVAL_DECISION_REF,
        "persona_readback_url": "http://persona-authority.test/personas/persona-alpha",
        "approval_readback_url": "http://governance.test/approvals/approval-alpha-4",
        "target_write_url": "http://persona-authority.test/personas/persona-alpha/targets",
        "target_readback_url": (
            "http://persona-authority.test/personas/persona-alpha/targets/generation-4"
        ),
        "authorization_token": AUTHORIZATION_TOKEN,
        "trusted_now": NOW,
    }
    return inputs, persona, approval, committed


def test_commit_persona_target_reads_authorities_writes_and_verifies_terminal() -> None:
    module = _load_module()
    inputs, persona, approval, committed = _scenario(module)
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, {"persona": persona}),
            module.PersonaTargetResponse(200, {"approval": approval}),
            module.PersonaTargetResponse(201, {"target": committed}),
            module.PersonaTargetResponse(200, {"target": committed}),
        ]
    )

    receipt = module.commit_persona_target(**inputs, transport=transport)

    assert receipt["status"] == "committed"
    assert receipt["generation"] == 4
    assert receipt["pre_generation"] == 3
    assert receipt["target_recorded_at"] == "2026-07-15T13:57:00Z"
    assert receipt["replayed"] is False
    assert [call["method"] for call in transport.calls] == ["GET", "GET", "POST", "GET"]
    assert all(
        call["headers"]["Authorization"] == f"Bearer {AUTHORIZATION_TOKEN}"
        for call in transport.calls
    )
    assert all(call["headers"]["X-Tenant-Id"] == TENANT_ID for call in transport.calls)
    write = transport.calls[2]
    assert write["headers"]["Idempotency-Key"] == inputs["idempotency_key"]
    assert write["json_body"]["candidate_digest"] == receipt["candidate_digest"]
    assert write["json_body"]["control_digest"] == receipt["control_digest"]
    assert write["json_body"]["proof_digest"] == receipt["proof_digest"]
    assert write["json_body"]["approval_digest"] == receipt["approval_digest"]
    assert write["json_body"]["expected_previous_generation"] == 3
    assert "approval" not in write["json_body"]
    assert "approved" not in write["json_body"]


def test_read_persona_target_precondition_is_authenticated_and_read_only() -> None:
    module = _load_module()
    inputs, persona, _, _ = _scenario(module)
    transport = _Transport([module.PersonaTargetResponse(200, {"persona": persona})])

    precondition = module.read_persona_target_precondition(
        persona_id=inputs["persona_id"],
        tenant_id=inputs["tenant_id"],
        persona_readback_url=inputs["persona_readback_url"],
        authorization_token=AUTHORIZATION_TOKEN,
        trusted_now=NOW,
        transport=transport,
    )

    assert precondition == {
        "persona_id": "persona-alpha",
        "tenant_id": TENANT_ID,
        "status": "active",
        "current_generation": 3,
        "expected_previous_generation": 3,
        "target_generation": 4,
        "precondition_digest": inputs["expected_precondition_digest"],
        "controller_record_ref": persona["controller_record_ref"],
        "recorded_at": "2026-07-15T13:54:00Z",
    }
    assert len(transport.calls) == 1
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["json_body"] is None
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {AUTHORIZATION_TOKEN}"


@pytest.mark.parametrize("token", ["", "   ", "token\r\nX-Evil: injected"])
def test_public_authority_calls_reject_missing_or_unsafe_bearer_token(token: str) -> None:
    module = _load_module()
    inputs, _, _, _ = _scenario(module)
    transport = _Transport([])

    with pytest.raises(module.PersonaTargetError) as error:
        module.commit_persona_target(
            **{**inputs, "authorization_token": token}, transport=transport
        )

    assert AUTHORIZATION_TOKEN not in str(error.value)
    if token:
        assert token not in str(error.value)
    assert transport.calls == []

    with pytest.raises(module.PersonaTargetError):
        module.read_persona_target_precondition(
            persona_id=inputs["persona_id"],
            tenant_id=inputs["tenant_id"],
            persona_readback_url=inputs["persona_readback_url"],
            authorization_token=token,
            trusted_now=NOW,
            transport=transport,
        )
    assert transport.calls == []


def test_transport_error_never_exposes_authorization_token() -> None:
    module = _load_module()
    inputs, _, _, _ = _scenario(module)
    transport = _Transport([RuntimeError(f"request headers contained {AUTHORIZATION_TOKEN}")])

    with pytest.raises(module.PersonaTargetError) as error:
        module.commit_persona_target(**inputs, transport=transport)

    assert AUTHORIZATION_TOKEN not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_commit_persona_target_rejects_negative_authoritative_approval() -> None:
    module = _load_module()
    inputs, persona, approval, _ = _scenario(module)
    approval = {**approval, "decision": "rejected"}
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, persona),
            module.PersonaTargetResponse(200, approval),
        ]
    )

    with pytest.raises(module.PersonaTargetError, match="outcome is not approved"):
        module.commit_persona_target(**inputs, transport=transport)

    assert [call["method"] for call in transport.calls] == ["GET", "GET"]


@pytest.mark.parametrize("record_name", ["persona", "approval", "write", "terminal"])
@pytest.mark.parametrize(
    "missing_field", ["authority_status", "controller_record_ref", "recorded_at"]
)
def test_every_controller_record_requires_authoritative_metadata(
    record_name: str, missing_field: str
) -> None:
    module = _load_module()
    inputs, persona, approval, committed = _scenario(module)
    records = {
        "persona": dict(persona),
        "approval": dict(approval),
        "write": dict(committed),
        "terminal": dict(committed),
    }
    records[record_name].pop(missing_field)
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, records["persona"]),
            module.PersonaTargetResponse(200, records["approval"]),
            module.PersonaTargetResponse(201, records["write"]),
            module.PersonaTargetResponse(200, records["terminal"]),
        ]
    )

    with pytest.raises(module.PersonaTargetError):
        module.commit_persona_target(**inputs, transport=transport)


def test_recorded_at_allows_configured_skew_but_rejects_later_future() -> None:
    module = _load_module()
    inputs, persona, _, _ = _scenario(module)
    within_skew = {**persona, "recorded_at": "2026-07-15T14:00:30Z"}
    allowed_transport = _Transport([module.PersonaTargetResponse(200, within_skew)])

    receipt = module.read_persona_target_precondition(
        persona_id=inputs["persona_id"],
        tenant_id=inputs["tenant_id"],
        persona_readback_url=inputs["persona_readback_url"],
        authorization_token=AUTHORIZATION_TOKEN,
        trusted_now=NOW,
        max_future_skew_seconds=60,
        transport=allowed_transport,
    )
    assert receipt["recorded_at"] == "2026-07-15T14:00:30Z"

    too_far = {**persona, "recorded_at": "2026-07-15T14:01:01Z"}
    rejected_transport = _Transport([module.PersonaTargetResponse(200, too_far)])
    with pytest.raises(module.PersonaTargetError, match="recorded_at is in the future"):
        module.read_persona_target_precondition(
            persona_id=inputs["persona_id"],
            tenant_id=inputs["tenant_id"],
            persona_readback_url=inputs["persona_readback_url"],
            authorization_token=AUTHORIZATION_TOKEN,
            trusted_now=NOW,
            max_future_skew_seconds=60,
            transport=rejected_transport,
        )


def test_naive_recorded_at_fails_closed() -> None:
    module = _load_module()
    inputs, persona, _, _ = _scenario(module)
    persona = {**persona, "recorded_at": "2026-07-15T13:54:00"}
    transport = _Transport([module.PersonaTargetResponse(200, persona)])

    with pytest.raises(module.PersonaTargetError, match="must include a timezone"):
        module.read_persona_target_precondition(
            persona_id=inputs["persona_id"],
            tenant_id=inputs["tenant_id"],
            persona_readback_url=inputs["persona_readback_url"],
            authorization_token=AUTHORIZATION_TOKEN,
            trusted_now=NOW,
            transport=transport,
        )


def test_approval_identity_must_match_expected_proof_policy_authority() -> None:
    module = _load_module()
    inputs, persona, approval, _ = _scenario(module)
    approval = {**approval, "approval_decision_ref": "approval-decision://wrong"}
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, persona),
            module.PersonaTargetResponse(200, approval),
        ]
    )

    with pytest.raises(module.PersonaTargetError, match="approval_decision_ref mismatch"):
        module.commit_persona_target(**inputs, transport=transport)

    mismatched_input = {
        **inputs,
        "approval_decision_ref": "approval-decision://different-proof-authority",
    }
    no_calls = _Transport([])
    with pytest.raises(module.PersonaTargetError, match="approval_decision_ref mismatch"):
        module.commit_persona_target(**mismatched_input, transport=no_calls)
    assert no_calls.calls == []


def test_commit_rejects_changed_persona_precondition_digest() -> None:
    module = _load_module()
    inputs, persona, _, _ = _scenario(module)
    changed_persona = {**persona, "recorded_at": "2026-07-15T13:54:01Z"}
    transport = _Transport([module.PersonaTargetResponse(200, changed_persona)])

    with pytest.raises(module.PersonaTargetError, match="pre-readback digest mismatch"):
        module.commit_persona_target(**inputs, transport=transport)

    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "nested_marker",
    [
        {"projection": {"source": "integration-fixture"}},
        {"projection": {"worker_status": "queued"}},
    ],
)
def test_nested_forbidden_marker_fails_closed(nested_marker: dict[str, Any]) -> None:
    module = _load_module()
    inputs, persona, approval, committed = _scenario(module)
    nested_fixture = {
        **committed,
        "controller_metadata": nested_marker,
    }
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, persona),
            module.PersonaTargetResponse(200, approval),
            module.PersonaTargetResponse(200, nested_fixture),
        ]
    )

    with pytest.raises(module.PersonaTargetError, match="non-authoritative marker"):
        module.commit_persona_target(**inputs, transport=transport)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "submitted"),
        ("status", "queued"),
        ("status", "pending"),
        ("source", "seed"),
        ("source", "persona-snapshot"),
        ("source", "test-fixture"),
    ],
)
def test_nonterminal_or_non_authoritative_write_never_succeeds(field: str, value: str) -> None:
    module = _load_module()
    inputs, persona, approval, committed = _scenario(module)
    invalid_write = {**committed, field: value}
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, persona),
            module.PersonaTargetResponse(200, approval),
            module.PersonaTargetResponse(200, invalid_write),
        ]
    )

    with pytest.raises(module.PersonaTargetError):
        module.commit_persona_target(**inputs, transport=transport)

    assert len(transport.calls) == 3


@pytest.mark.parametrize(
    "field",
    [
        "candidate_digest",
        "control_digest",
        "proof_digest",
        "approval_digest",
        "generation",
    ],
)
def test_terminal_readback_must_match_every_digest_and_generation(field: str) -> None:
    module = _load_module()
    inputs, persona, approval, committed = _scenario(module)
    mismatch = dict(committed)
    mismatch[field] = 5 if field == "generation" else "0" * 64
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, persona),
            module.PersonaTargetResponse(200, approval),
            module.PersonaTargetResponse(201, committed),
            module.PersonaTargetResponse(200, mismatch),
        ]
    )

    with pytest.raises(module.PersonaTargetError, match=f"{field} mismatch"):
        module.commit_persona_target(**inputs, transport=transport)


def test_transport_timeout_and_missing_url_fail_closed() -> None:
    module = _load_module()
    inputs, _, _, _ = _scenario(module)
    timed_out = _Transport([TimeoutError("authority timed out")])

    with pytest.raises(module.PersonaTargetError, match="transport failed"):
        module.commit_persona_target(**inputs, transport=timed_out)

    missing = _Transport([])
    with pytest.raises(module.PersonaTargetError, match="approval_readback_url is required"):
        module.commit_persona_target(
            **{**inputs, "approval_readback_url": ""}, transport=missing
        )
    assert missing.calls == []


@pytest.mark.parametrize(
    "response",
    [
        lambda module: module.PersonaTargetResponse(404, {"detail": "not found"}),
        lambda module: module.PersonaTargetResponse(200, "not-json"),
    ],
)
def test_http_error_or_non_json_authority_fails_closed(response) -> None:
    module = _load_module()
    inputs, _, _, _ = _scenario(module)
    transport = _Transport([response(module)])

    with pytest.raises(module.PersonaTargetError):
        module.commit_persona_target(**inputs, transport=transport)


@pytest.mark.parametrize(
    "timestamp_patch",
    [
        {"decided_at": "2026-07-15T14:00:01Z"},
        {"expires_at": "2026-07-15T14:00:00Z"},
    ],
)
def test_future_or_expired_approval_fails_closed(timestamp_patch: dict[str, str]) -> None:
    module = _load_module()
    inputs, persona, approval, _ = _scenario(module)
    approval = {**approval, **timestamp_patch}
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, persona),
            module.PersonaTargetResponse(200, approval),
        ]
    )

    with pytest.raises(module.PersonaTargetError):
        module.commit_persona_target(**inputs, transport=transport)


def test_duplicate_commit_reuses_idempotency_key_and_generation() -> None:
    module = _load_module()
    inputs, first_persona, approval, committed = _scenario(module)
    replay_persona = dict(committed)
    replayed_write = {**committed, "replayed": True}
    transport = _Transport(
        [
            module.PersonaTargetResponse(200, first_persona),
            module.PersonaTargetResponse(200, approval),
            module.PersonaTargetResponse(201, committed),
            module.PersonaTargetResponse(200, committed),
            module.PersonaTargetResponse(200, replay_persona),
            module.PersonaTargetResponse(200, approval),
            module.PersonaTargetResponse(200, replayed_write),
            module.PersonaTargetResponse(200, committed),
        ]
    )

    first = module.commit_persona_target(**inputs, transport=transport)
    duplicate = module.commit_persona_target(**inputs, transport=transport)

    assert first["replayed"] is False
    assert duplicate["replayed"] is True
    assert duplicate["target_recorded_at"] == first["target_recorded_at"]
    writes = [call for call in transport.calls if call["method"] == "POST"]
    assert len(writes) == 2
    assert writes[0]["headers"]["Idempotency-Key"] == writes[1]["headers"]["Idempotency-Key"]
    assert writes[0]["json_body"] == writes[1]["json_body"]
    assert writes[0]["json_body"]["generation"] == 4
