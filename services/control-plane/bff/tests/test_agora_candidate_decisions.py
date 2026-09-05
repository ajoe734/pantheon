from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.control_plane.bff.agora.candidate_decisions.models import (
    CandidateDecisionCommand,
    CandidateFromMeasureCommand,
    canonical_sha256,
)
from services.control_plane.bff.agora.candidate_decisions.service import CandidateDecisionService
from services.control_plane.bff.agora.candidate_decisions.store import CandidateDecisionConflict, CandidateDecisionStore
from services.control_plane.bff.agora.candidate_decisions.adapters import (
    CANONICAL_VALIDATOR_ID,
    CandidateBindingValidationAdapter,
    ReadStoreApprovalAdapter,
)
from services.control_plane.bff.agora.candidate_decisions.router import create_candidate_decision_router
from services.control_plane.bff.agora.interaction.provider import RecommendedMeasure, authority_boundary
from services.control_plane.bff.agora.interaction.provider import recommended_measure_sha256
from services.control_plane.bff.agora.interaction.store import InteractionLifecycleStore


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _measure(*, proposed_value: Any = 0.08) -> dict[str, Any]:
    return {
        "measure_id": "measure-risk-1",
        "measure_type": "risk_limit_recommendation",
        "target": {
            "kind": "strategy",
            "id": "strategy-1",
            "version": "version-7",
            "path": "risk.max_position",
        },
        "current_value": 0.1,
        "proposed_value": proposed_value,
        "rationale": "Observed drawdown requires a smaller paper risk limit.",
        "expected_benefit": "Reduce tail loss during the paper validation window.",
        "adverse_scenarios": ["The tighter limit may suppress valid entries."],
        "confidence": 0.73,
        "evidence_refs": [
            {
                "ref_type": "journal_entry",
                "ref_id": "journal-44",
                "version": "v2",
                "observed_at": NOW.isoformat(),
                "data_cutoff": NOW.isoformat(),
                "freshness": "fresh",
                "summary": "Three stopped paper trades.",
            }
        ],
        "environment_ceiling": "paper",
        "validation_plan": {
            "validator": CANONICAL_VALIDATOR_ID,
            "required_checks": ["source_binding", "evidence_freshness", "target_version"],
        },
        "rollback_trigger": "Paper drawdown is not improved.",
        "rollback_action": "Retain the current strategy revision.",
        "authority": authority_boundary(),
    }


def _canonical_measure(**kwargs: Any) -> dict[str, Any]:
    measure = RecommendedMeasure.model_validate(_measure(**kwargs)).model_dump(mode="json")
    measure["measure_sha256"] = recommended_measure_sha256(measure)
    return measure


def _interaction(*, tenant_id: str = "tenant-a", topic: str = "human topic must not win") -> dict[str, Any]:
    measure = _canonical_measure()
    return {
        "interaction_id": "interaction-1",
        "tenant_id": tenant_id,
        "human_request": {"request_text": topic},
        "context_snapshot": {"tenant_id": tenant_id},
        "opinions": [
            {
                "opinion_id": "opinion-1",
                "interaction_id": "interaction-1",
                "participant": {
                    "persona_id": "persona-risk",
                    "persona_version": "persona-v3",
                },
                "provider_invocation_id": "invocation-openclaw-1",
                "conclusion": "conditional",
                "rationale": "Use a smaller paper-only limit.",
                "recommended_measures": [measure],
                "authority": authority_boundary(),
                "provenance": {
                    "content_origin": "selected_persona_provider_response",
                    "provider_kind": "openclaw",
                    "provider_invocation_id": "invocation-openclaw-1",
                    "request_correlated": True,
                    "response_correlated": True,
                    "canned_template": False,
                    "magic_topic_trigger": False,
                    "simulation": False,
                },
            }
        ],
    }


def _command(interaction: dict[str, Any] | None = None) -> CandidateFromMeasureCommand:
    return CandidateFromMeasureCommand(
        interaction_id="interaction-1",
        opinion_id="opinion-1",
        measure_id="measure-risk-1",
    )


def _service(validation_adapter=None, approval_store=None) -> CandidateDecisionService:
    interactions = InteractionLifecycleStore(backend="memory")
    return CandidateDecisionService(
        CandidateDecisionStore(backend="off"),
        interaction_store=interactions,
        validation_adapter=validation_adapter or _ValidationAdapter(),
        approval_store=approval_store or _ApprovalStore(None),
        clock=lambda: NOW,
    )


def test_store_has_no_implicit_process_local_runtime_fallback(monkeypatch) -> None:
    monkeypatch.delenv("AGORA_CANDIDATE_DECISION_STORE_BACKEND", raising=False)
    with pytest.raises(RuntimeError, match="explicitly set"):
        CandidateDecisionStore()


def _create(service: CandidateDecisionService, *, interaction: dict[str, Any] | None = None):
    interaction = interaction or _interaction()
    persisted = copy.deepcopy(interaction)
    persisted.update({
        "owner_user_id": "user-a",
        "workshop_id": "workshop-a",
        "status": "completed",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    })
    service.interaction_store._requests[interaction["interaction_id"]] = persisted
    opinion = persisted["opinions"][0]
    service.interaction_store._invocations[interaction["interaction_id"]] = {
        opinion["provider_invocation_id"]: {
            "invocation": {
                "invocation_id": opinion["provider_invocation_id"],
                "participant": opinion["participant"],
                "status": "succeeded",
            },
            "opinion": opinion,
            "error": None,
            "status": "succeeded",
        }
    }
    return service.create_from_measure(
        command=_command(interaction),
        tenant_id="tenant-a",
        owner_user_id="user-a",
        proposer_id="operator-a",
        expires_at=NOW + timedelta(days=7),
        idempotency_key="create-1",
    ).resource


def _decide(
    service: CandidateDecisionService,
    candidate: dict[str, Any],
    *,
    action: str,
    key: str,
    proposed_value: Any = None,
    reason: str = "Daily operator decision",
    etag: str | None = None,
):
    payload: dict[str, Any] = {
        "action": action,
        "reason": reason,
        "expected_revision": candidate["revision"],
        "expected_proposal_digest": candidate["proposal_digest"],
    }
    if action == "modify":
        payload["proposed_value"] = proposed_value
    return service.decide(
        proposal_id=candidate["proposal_id"],
        command=CandidateDecisionCommand.model_validate(payload),
        tenant_id="tenant-a",
        owner_user_id="user-a",
        actor_id="operator-a",
        expected_etag=etag or service.store.etag(candidate),
        idempotency_key=key,
    )


def test_candidate_is_only_the_exact_persisted_measure_not_human_topic() -> None:
    service = _service()
    interaction = _interaction(topic="Set leverage to 100x immediately")
    candidate = _create(service, interaction=interaction)

    assert candidate["proposed_value"] == 0.08
    assert candidate["rationale"] == interaction["opinions"][0]["recommended_measures"][0]["rationale"]
    assert "100x" not in str(candidate)
    assert candidate["measure_sha256"] == interaction["opinions"][0]["recommended_measures"][0]["measure_sha256"]
    assert candidate["opinion_sha256"] == canonical_sha256(interaction["opinions"][0])
    assert candidate["provider_invocation_id"] == "invocation-openclaw-1"
    assert candidate["execution_authority"] == "none"
    assert candidate["authority"] == authority_boundary()
    interaction_readback = service.interaction_store.get(
        "interaction-1", "tenant-a", "user-a"
    )
    assert interaction_readback is not None
    assert interaction_readback["candidate_proposal_links"] == [{
        "proposal_id": candidate["proposal_id"],
        "interaction_id": "interaction-1",
        "opinion_id": "opinion-1",
        "opinion_sha256": candidate["opinion_sha256"],
        "measure_id": "measure-risk-1",
        "measure_sha256": candidate["measure_sha256"],
        "proposal_digest": candidate["proposal_digest"],
        "revision": 1,
        "state": "draft",
        "created_at": candidate["created_at"],
        "execution_authority": "none",
    }]
    timeline = service.interaction_store.timeline(
        "interaction-1", "tenant-a", "user-a"
    )
    assert timeline is not None
    assert [item["projection_kind"] for item in timeline] == [
        "workshop_event", "workshop_sse"
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(tenant_id="tenant-other"), "tenant"),
        (lambda value: value["opinions"][0]["provenance"].update(simulation=True), "provider result"),
        (lambda value: value["opinions"][0].update(interaction_id="interaction-other"), "interaction binding"),
        (lambda value: value["opinions"][0]["participant"].update(persona_version=""), "provenance"),
    ],
)
def test_candidate_source_fails_closed_for_invalid_persisted_truth(mutate, message) -> None:
    service = _service()
    interaction = _interaction()
    mutate(interaction)
    with pytest.raises((ValueError, CandidateDecisionConflict), match=message):
        _create(service, interaction=interaction)


def test_measure_digest_and_ambiguity_fail_closed() -> None:
    service = _service()
    interaction = _interaction()
    interaction["opinions"][0]["recommended_measures"][0]["measure_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        _create(service, interaction=interaction)
    interaction["opinions"][0]["recommended_measures"].append(
        copy.deepcopy(interaction["opinions"][0]["recommended_measures"][0])
    )
    with pytest.raises(ValueError, match="ambiguous"):
        _create(service, interaction=interaction)


def test_browser_supplied_validation_result_is_not_a_command_field() -> None:
    with pytest.raises(ValidationError, match="validation_result"):
        CandidateDecisionCommand.model_validate(
            {
                "action": "accept_for_review",
                "reason": "Review it",
                "expected_revision": 1,
                "expected_proposal_digest": "a" * 64,
                "validation_result": {"passed": True},
            }
        )


def test_modify_is_an_immutable_revision_with_exact_audit_and_digest() -> None:
    service = _service()
    initial = _create(service)
    result = _decide(service, initial, action="modify", proposed_value=0.06, key="modify-1")
    revised = result.resource["candidate"]
    decision = result.resource["decision"]

    assert revised["revision"] == 2
    assert revised["proposed_value"] == 0.06
    assert revised["proposal_digest"] != initial["proposal_digest"]
    assert decision["action"] == "modified"
    assert decision["revision"] == revised["revision"]
    assert decision["proposal_digest"] == revised["proposal_digest"]
    assert decision["measure_sha256"] == initial["measure_sha256"]
    assert service.store.history(initial["proposal_id"], "tenant-a", "user-a")[0] == initial
    assert service.store.decisions(initial["proposal_id"], "tenant-a", "user-a") == [decision]
    readback = service.readback(
        proposal_id=initial["proposal_id"], tenant_id="tenant-a", owner_user_id="user-a"
    )
    assert readback["candidate"] == revised
    assert readback["revisions"] == [initial, revised]
    assert readback["decisions"] == [decision]
    assert readback["validation_receipts"] == []
    assert readback["formal_approval_receipts"] == []
    assert readback["etag"] == service.store.etag(revised)
    assert readback["execution_authority"] == "none"


@pytest.mark.parametrize(
    ("action", "state", "record_action"),
    [
        ("accept_for_review", "review_requested", "accepted_for_review"),
        ("reject", "rejected", "rejected"),
        ("defer", "deferred", "deferred"),
        ("cancel", "cancelled", "cancelled"),
    ],
)
def test_daily_decisions_are_durable_non_approval_non_execution(action, state, record_action) -> None:
    service = _service()
    initial = _create(service)
    result = _decide(service, initial, action=action, key=f"decision-{action}")
    candidate = result.resource["candidate"]
    decision = result.resource["decision"]

    assert candidate["state"] == state
    assert decision["action"] == record_action
    assert decision["formal_approval"] is False
    assert decision["execution_authority"] == "none"
    assert bool(decision["review_request_id"]) is (action == "accept_for_review")


def test_decision_etag_revision_digest_scope_expiry_and_terminal_fail_closed() -> None:
    service = _service()
    initial = _create(service)
    with pytest.raises(CandidateDecisionConflict, match="ETag"):
        _decide(service, initial, action="defer", key="stale-etag", etag='"stale"')
    stale_revision = CandidateDecisionCommand(
        action="defer", reason="later", expected_revision=2,
        expected_proposal_digest=initial["proposal_digest"],
    )
    with pytest.raises(CandidateDecisionConflict, match="revision"):
        service.decide(
            proposal_id=initial["proposal_id"], command=stale_revision,
            tenant_id="tenant-a", owner_user_id="user-a", actor_id="operator-a",
            expected_etag=service.store.etag(initial), idempotency_key="stale-revision",
        )
    wrong_digest = stale_revision.model_copy(
        update={"expected_revision": 1, "expected_proposal_digest": "b" * 64}
    )
    with pytest.raises(CandidateDecisionConflict, match="digest"):
        service.decide(
            proposal_id=initial["proposal_id"], command=wrong_digest,
            tenant_id="tenant-a", owner_user_id="user-a", actor_id="operator-a",
            expected_etag=service.store.etag(initial), idempotency_key="stale-digest",
        )
    with pytest.raises(CandidateDecisionConflict, match="scope"):
        service.decide(
            proposal_id=initial["proposal_id"], command=CandidateDecisionCommand(
                action="defer", reason="later", expected_revision=1,
                expected_proposal_digest=initial["proposal_digest"],
            ), tenant_id="tenant-other", owner_user_id="user-a", actor_id="operator-a",
            expected_etag=service.store.etag(initial), idempotency_key="wrong-tenant",
        )
    rejected = _decide(service, initial, action="reject", key="reject-terminal").resource["candidate"]
    with pytest.raises(CandidateDecisionConflict, match="terminal"):
        _decide(service, rejected, action="modify", proposed_value=0.03, key="after-terminal")


def test_idempotency_replays_exact_response_and_rejects_payload_change() -> None:
    service = _service()
    initial = _create(service)
    first = _decide(service, initial, action="modify", proposed_value=0.06, key="idem-1")
    replay = _decide(service, initial, action="modify", proposed_value=0.06, key="idem-1")
    assert replay.replayed is True
    assert replay.resource == first.resource
    with pytest.raises(CandidateDecisionConflict, match="idempotency"):
        _decide(service, initial, action="modify", proposed_value=0.05, key="idem-1")


class _ValidationAdapter:
    adapter_id = "validator-prod"

    def __init__(self, mutate=None) -> None:
        self.mutate = mutate
        self.request = None
        self.plan = None

    def readiness(self, *, candidate):
        return {
            "ready": self.adapter_id == "validator-prod",
            "reason": None if self.adapter_id == "validator-prod" else "adapter_not_registered",
        }

    def validate(self, request, *, validation_plan, candidate):
        self.request = request
        self.plan = validation_plan
        payload = {
            "validation_receipt_id": "validation-1",
            "authority": "canonical_validation_service",
            "tenant_id": "tenant-a",
            "proposal_id": request.proposal_id,
            "revision": request.revision,
            "proposal_digest": request.proposal_digest,
            "outcome": "passed",
            "evidence_refs": ["validator-run:44"],
            "validated_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(days=1)).isoformat(),
        }
        if self.mutate:
            self.mutate(payload)
        payload["receipt_sha256"] = canonical_sha256(payload)
        return payload


def _accepted(service: CandidateDecisionService) -> dict[str, Any]:
    return _decide(
        service, _create(service), action="accept_for_review", key="accept-validation"
    ).resource["candidate"]


def _validate(service, candidate, adapter=None, *, key="validate-1", etag=None):
    if adapter is not None:
        service.validation_adapter = adapter
    return service.run_authoritative_validation(
        proposal_id=candidate["proposal_id"], tenant_id="tenant-a", owner_user_id="user-a",
        expected_revision=candidate["revision"], expected_proposal_digest=candidate["proposal_digest"],
        expected_etag=etag or service.store.etag(candidate), idempotency_key=key,
    )


def test_validation_is_server_adapter_derived_and_durable() -> None:
    service = _service()
    candidate = _accepted(service)
    adapter = _ValidationAdapter()
    stored = _validate(service, candidate, adapter).resource

    assert adapter.request.validation_plan_ref == f"sha256:{canonical_sha256(candidate['validation_plan'])}"
    assert adapter.plan == candidate["validation_plan"]
    assert stored["authority"] == "canonical_validation_service"
    assert stored["revision"] == candidate["revision"]
    assert stored["proposal_digest"] == candidate["proposal_digest"]
    assert service.store.validation_receipts(candidate["proposal_id"], "tenant-a", "user-a") == [stored]


def test_validation_rejects_untrusted_adapter_state_and_bad_receipts() -> None:
    service = _service()
    draft = _create(service)
    with pytest.raises(CandidateDecisionConflict, match="accept-for-review"):
        _validate(service, draft)
    accepted = _decide(service, draft, action="accept_for_review", key="accept-2").resource["candidate"]
    untrusted = _ValidationAdapter()
    untrusted.adapter_id = "browser-validator"
    with pytest.raises(CandidateDecisionConflict, match="not ready"):
        _validate(service, accepted, untrusted, key="untrusted")
    for mutate, message in [
        (lambda value: value.update(tenant_id="tenant-other"), "tenant"),
        (lambda value: value.update(proposal_digest="f" * 64), "digest"),
        (lambda value: value.update(expires_at=(NOW + timedelta(days=8)).isoformat()), "expiry"),
    ]:
        with pytest.raises(CandidateDecisionConflict, match=message):
            _validate(service, accepted, _ValidationAdapter(mutate), key=f"bad-{message}")


class _ApprovalStore:
    def __init__(self, record: dict[str, Any] | None, *, fail: bool = False) -> None:
        self.record = record
        self.fail = fail

    def get_formal_approval(self, approval_decision_id: str):
        if self.fail:
            raise RuntimeError("unavailable")
        if self.record and self.record["approval_decision_id"] == approval_decision_id:
            return self.record
        return None

    def readiness(self):
        return {
            "ready": not self.fail,
            "reason": "approval_store_unavailable" if self.fail else None,
        }


def _approval(candidate, validation, **changes):
    payload = {
        "approval_decision_id": "approval-1",
        "authority": "canonical_approval_decision_store",
        "tenant_id": "tenant-a",
        "proposal_id": candidate["proposal_id"],
        "revision": candidate["revision"],
        "proposal_digest": candidate["proposal_digest"],
        "validation_receipt_id": validation["validation_receipt_id"],
        "validation_receipt_sha256": validation["receipt_sha256"],
        "proposer_id": "operator-a",
        "reviewer_id": "operator-b",
        "outcome": "approved",
        "self_approval": False,
        "decided_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "execution_authority": "none",
    }
    payload.update(changes)
    payload["receipt_sha256"] = canonical_sha256(payload)
    return payload


def _link(service, candidate, approval, *, key="approval-link-1", etag=None):
    service.approval_store = _ApprovalStore(approval)
    return service.link_formal_approval(
        proposal_id=candidate["proposal_id"], approval_decision_id=approval["approval_decision_id"],
        tenant_id="tenant-a", owner_user_id="user-a", expected_revision=candidate["revision"],
        expected_proposal_digest=candidate["proposal_digest"],
        expected_etag=etag or service.store.etag(candidate), idempotency_key=key,
    )


def test_formal_approval_is_distinct_canonical_linkage_and_non_executing() -> None:
    service = _service()
    candidate = _accepted(service)
    validation = _validate(service, candidate).resource
    approval = _approval(candidate, validation)
    stored = _link(service, candidate, approval).resource

    assert stored["approval_decision_id"] == "approval-1"
    assert stored["reviewer_id"] != stored["proposer_id"]
    assert stored["execution_authority"] == "none"
    assert service.store.approval_receipts(candidate["proposal_id"], "tenant-a", "user-a") == [stored]
    readback = service.readback(
        proposal_id=candidate["proposal_id"], tenant_id="tenant-a", owner_user_id="user-a"
    )
    assert readback["validation_receipts"] == [validation]
    assert readback["formal_approval_receipts"] == [stored]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"tenant_id": "tenant-other"}, "tenant"),
        ({"revision": 1}, "revision"),
        ({"proposal_digest": "c" * 64}, "digest"),
        ({"reviewer_id": "operator-a"}, "self-approval"),
        ({"validation_receipt_id": "validation-other"}, "validation receipt"),
        ({"expires_at": (NOW + timedelta(days=9)).isoformat()}, "expiry"),
    ],
)
def test_formal_approval_exact_binding_negative_cases(changes, message) -> None:
    service = _service()
    candidate = _accepted(service)
    validation = _validate(service, candidate).resource
    approval = _approval(candidate, validation, **changes)
    with pytest.raises((CandidateDecisionConflict, ValidationError), match=message):
        _link(service, candidate, approval, key=f"approval-bad-{message}")


def test_formal_approval_requires_current_validation_canonical_store_etag_and_idempotency() -> None:
    service = _service()
    candidate = _accepted(service)
    approval_without_validation = _approval(
        candidate,
        {"validation_receipt_id": "none", "receipt_sha256": "d" * 64},
    )
    with pytest.raises(CandidateDecisionConflict, match="validation"):
        _link(service, candidate, approval_without_validation, key="no-validation")
    validation = _validate(service, candidate).resource
    approval = _approval(candidate, validation)
    with pytest.raises(CandidateDecisionConflict, match="ETag"):
        _link(service, candidate, approval, key="bad-etag", etag='"stale"')
    first = _link(service, candidate, approval, key="approval-replay")
    replay = _link(service, candidate, approval, key="approval-replay")
    assert replay.replayed is True
    assert replay.resource == first.resource
    with pytest.raises(CandidateDecisionConflict, match="canonical"):
        service.link_formal_approval(
            proposal_id=candidate["proposal_id"], approval_decision_id="missing",
            tenant_id="tenant-a", owner_user_id="user-a", expected_revision=candidate["revision"],
            expected_proposal_digest=candidate["proposal_digest"],
            expected_etag=service.store.etag(candidate), idempotency_key="missing",
        )


def test_formal_approval_rejects_revoked_or_stale_after_candidate_revision() -> None:
    service = _service()
    candidate = _accepted(service)
    validation = _validate(service, candidate).resource
    revoked = _approval(candidate, validation)
    revoked["revoked_at"] = NOW.isoformat()
    revoked["receipt_sha256"] = canonical_sha256(
        {key: value for key, value in revoked.items() if key != "receipt_sha256"}
    )
    with pytest.raises(CandidateDecisionConflict, match="revoked"):
        _link(service, candidate, revoked, key="approval-revoked")

    revised = _decide(
        service, candidate, action="modify", proposed_value=0.04, key="modify-after-validation"
    ).resource["candidate"]
    stale_approval = _approval(revised, validation)
    with pytest.raises(CandidateDecisionConflict, match="current authoritative passed validation"):
        _link(service, revised, stale_approval, key="approval-stale-validation")


def test_server_owned_binding_validator_passes_only_exact_current_candidate() -> None:
    adapter = CandidateBindingValidationAdapter(clock=lambda: NOW)
    service = _service(validation_adapter=adapter)
    candidate = _accepted(service)
    receipt = _validate(service, candidate).resource
    assert receipt["outcome"] == "passed"
    assert receipt["authority"] == "canonical_validation_service"
    assert receipt["proposal_digest"] == candidate["proposal_digest"]


class _CanonicalApprovalReadStore:
    def __init__(self, *, available: bool, record: dict[str, Any] | None) -> None:
        self.available = available
        self.record = record
        self.local_fixture = {"approval-1": {"outcome": "approved"}}

    def get_canonical_approval_decision_readback(self, decision_id: str):
        return {
            "available": self.available,
            "record": self.record if decision_id != "__candidate_readiness_probe__" else None,
            "source": "canonical.approval_decision",
        }


def test_approval_adapter_rejects_local_fallback_and_preserves_exact_canonical_receipt() -> None:
    unavailable = ReadStoreApprovalAdapter(
        lambda: _CanonicalApprovalReadStore(available=False, record=None)
    )
    assert unavailable.readiness()["ready"] is False
    with pytest.raises(RuntimeError, match="unavailable"):
        unavailable.get_formal_approval("approval-1")

    exact = {"approval_decision_id": "approval-1", "receipt_sha256": "a" * 64}
    available = ReadStoreApprovalAdapter(
        lambda: _CanonicalApprovalReadStore(available=True, record=exact)
    )
    assert available.readiness()["ready"] is True
    assert available.get_formal_approval("approval-1") == exact


def test_candidate_routes_return_reload_arrays_and_rotate_full_record_etag(monkeypatch) -> None:
    from services.control_plane.bff.models import OperatorIdentity

    service = _service()
    candidate = _create(service)
    identity = OperatorIdentity(
        operator_id="operator-a",
        roles=["operator"],
        claims={"tenant_id": "tenant-a", "sub": "user-a"},
    )
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-a")
    app = FastAPI()
    app.include_router(create_candidate_decision_router(
        service=service,
        extract_identity=lambda _auth: identity,
        require_read_role=lambda _identity: None,
        require_write_role=lambda _identity: None,
        bff_error=lambda *args, **kwargs: HTTPException(args[0], detail=str(args[2])),
        utc_now=lambda: NOW.isoformat(),
    ))
    client = TestClient(app)
    headers = {"Authorization": "Bearer test", "X-Tenant-Id": "tenant-a"}
    detail = client.get(
        f"/bff/agora/proposals/{candidate['proposal_id']}/candidate", headers=headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.headers["etag"] == service.store.etag(candidate)
    assert detail.json()["data"]["decisions"] == []
    assert isinstance(detail.json()["data"]["revisions"], list)
    mutation = client.post(
        f"/bff/agora/proposals/{candidate['proposal_id']}/candidate-decisions",
        headers={
            **headers,
            "If-Match": detail.headers["etag"],
            "Idempotency-Key": "route-modify-1",
        },
        json={
            "action": "modify",
            "reason": "Daily review",
            "expected_revision": 1,
            "expected_proposal_digest": candidate["proposal_digest"],
            "proposed_value": 0.05,
        },
    )
    assert mutation.status_code == 200, mutation.text
    assert mutation.headers["etag"] != detail.headers["etag"]
    assert len(mutation.json()["data"]["decisions"]) == 1
    assert mutation.json()["data"]["execution_authority"] == "none"


def test_candidate_create_route_replays_when_server_expiry_clock_advances(monkeypatch) -> None:
    from services.control_plane.bff.models import OperatorIdentity

    service = _service()
    _create(service)
    identity = OperatorIdentity(
        operator_id="operator-a",
        roles=["operator"],
        claims={"tenant_id": "tenant-a", "sub": "user-a"},
    )
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-a")
    app = FastAPI()
    app.include_router(create_candidate_decision_router(
        service=service,
        extract_identity=lambda _auth: identity,
        require_read_role=lambda _identity: None,
        require_write_role=lambda _identity: None,
        bff_error=lambda *args, **kwargs: HTTPException(args[0], detail=str(args[2])),
        utc_now=lambda: NOW.isoformat(),
    ))
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer test",
        "X-Tenant-Id": "tenant-a",
        "Idempotency-Key": "route-create-replay-1",
    }
    path = (
        "/bff/agora/interactions/interaction-1/recommended-measures/"
        "measure-risk-1/candidates"
    )
    body = {
        "interaction_id": "interaction-1",
        "opinion_id": "opinion-1",
        "measure_id": "measure-risk-1",
    }

    created = client.post(path, headers=headers, json=body)
    replayed = client.post(path, headers=headers, json=body)

    assert created.status_code == 201, created.text
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == created.json()
    assert replayed.headers["etag"] == created.headers["etag"]
