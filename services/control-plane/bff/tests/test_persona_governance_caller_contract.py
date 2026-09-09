"""Governance caller contract for paper Persona provisioning approvals.

The approval bodies under test are the exact bodies
``PersonaProvisioningCoordinator`` composes for a real provisioning record.
They are checked against the authoritative owner code that is pure enough to
run without hosted services or credentials: strict Governance's wire models,
the canonical ``ApprovalDecision`` domain transitions, the shared
``ApprovalEvidence`` verifier that Registry and Deployment call, and Registry's
StrategySpec revision lineage rule.

Nothing here is a hosted receipt.  ``Idempotency-Key`` is a transport header
that root derives from method, path, tenant and the canonical body; this suite
only proves the bodies stay byte-identical across retries so that derivation
is stable.
"""
from __future__ import annotations

import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services.control_plane.bff.persona_provisioning_coordinator import (
    APPROVAL_TTL,
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _coordinator,
    _owner_entry,
    _plus_24h,
    _post_payload,
    _record_and_store,
    _schedule_receipt,
)
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid
from services.governance.models import (
    AcceptReviewRequest,
    DecideRequest,
    ProposeApprovalRequest,
)
from services.registry.models import Lineage, RegistryEntry
from services.registry.service import (
    RegistryConflictError,
    RegistryError,
    _check_strategy_spec_version_lineage,
    _strategy_spec_checksum,
)

_GOVERNANCE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "governance")
)
if _GOVERNANCE_DIR not in sys.path:
    sys.path.insert(0, _GOVERNANCE_DIR)
from approval_decision import ApprovalDecision, EvidenceRef  # noqa: E402


SERVICE_ACTOR = "control-plane-bff"
# A tenant-scoped automation subject that is NOT the dedicated dev paper
# grant: strict Governance handles it through the generic write-authority
# roles, so these tests cover subject separation without the paper scope.
# The dedicated ``pantheon-dev-paper-provisioner`` grant (owner-stamped
# authorization_scope) is exercised against the real Registry facade below.
GOVERNANCE_SUBJECT = "tenant-dev-approval-automation"
HUMAN_REQUESTER = "operator-a"


def _approval_paths(ids) -> list[tuple[str, str, str]]:
    return [
        (ids.baseline_approval_decision_id, "strategy-specs", ids.baseline_registry_id),
        (
            ids.baseline_strategy_artifact_approval_decision_id,
            "strategy-artifacts",
            ids.baseline_strategy_artifact_id,
        ),
        (ids.approval_decision_id, "strategy-specs", ids.registry_id),
        (
            ids.strategy_artifact_approval_decision_id,
            "strategy-artifacts",
            ids.strategy_artifact_id,
        ),
    ]


def _governance_posts(transport: FakeOwnerTransport) -> list[tuple[str, dict[str, Any]]]:
    return [
        (call[2], call[3])
        for call in transport.calls
        if call[0] == "POST" and call[1] == "governance" and call[3] is not None
    ]


def _contains_value(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_value(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, needle) for item in value)
    return value == needle


def _separate_principal_run() -> tuple[Any, Any, FakeOwnerTransport, Any]:
    store, record = _record_and_store()
    transport = FakeOwnerTransport(governance_subject=GOVERNANCE_SUBJECT)
    result = _coordinator(
        store,
        transport,
        _schedule_receipt,
        actor_id=SERVICE_ACTOR,
        governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(record)
    return store, record, transport, result


def _replay_domain_decision(
    proposal_body: dict[str, Any],
    review_body: dict[str, Any],
    decide_body: dict[str, Any],
) -> ApprovalDecision:
    """Apply the coordinator's bodies exactly as Governance ``_approval_command`` does."""

    proposal = ProposeApprovalRequest(**proposal_body)
    assert proposal.expected_version == 0
    decision = ApprovalDecision.create_proposed(
        decision_id=proposal.decision_id,
        **proposal.model_dump(mode="json", exclude={"expected_version", "decision_id"}),
    )
    assert decision.validate() == []
    decision.version = 1

    review = AcceptReviewRequest(**review_body)
    assert review.expected_version == decision.version
    decision.accept_review(actor_role=review.actor_role.value, actor_id=review.actor_id)
    assert decision.validate() == []
    decision.version = 2

    decide = DecideRequest(**decide_body)
    assert decide.expected_version == decision.version
    fields = decide.model_dump(
        mode="json",
        exclude={"expected_version", "actor_role", "actor_id", "evidence_refs"},
    )
    refs = [EvidenceRef(**ref.model_dump()) for ref in (decide.evidence_refs or [])]
    decision.decide(
        actor_role=decide.actor_role.value,
        actor_id=decide.actor_id,
        evidence_refs=refs or None,
        **fields,
    )
    assert decision.validate() == []
    decision.version = 3
    decision.event_id = f"event-{decision.decision_id}-3"
    return decision


def test_governance_subject_is_separate_from_service_actor_and_human_requester() -> None:
    _store, record, transport, result = _separate_principal_run()
    ids = deterministic_provisioning_ids(record)

    assert result.state == "provisioning"
    assert result.current_step == "schedule_registered"

    governance_posts = _governance_posts(transport)
    assert len(governance_posts) == 12
    for path, body in governance_posts:
        if path == "/api/governance/approvals":
            assert body["owner_user_id"] == GOVERNANCE_SUBJECT
        else:
            assert body["actor_id"] == GOVERNANCE_SUBJECT
            assert body["actor_role"] == "automated_gate"
        # No forged human actor and no leaked BFF service identity reach
        # Governance in any field.
        assert not _contains_value(body, HUMAN_REQUESTER)
        assert not _contains_value(body, SERVICE_ACTOR)

    for decision_id, _kind, _registry_id in _approval_paths(ids):
        persisted = transport.objects[("governance", f"/api/governance/approvals/{decision_id}")]
        assert persisted["owner_user_id"] == GOVERNANCE_SUBJECT
        assert persisted["actor_id"] == GOVERNANCE_SUBJECT
        assert persisted["decision"] == "approved"

    # Every other owner write keeps the BFF service actor, with the human
    # requester preserved as audit metadata only.
    pool = _post_payload(transport, "/api/capital-pools")
    binding = _post_payload(transport, "/api/bindings")
    plan = _post_payload(transport, "/api/deployment/plans")
    dispatch = _post_payload(
        transport,
        f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch",
    )
    assert pool["actor_id"] == SERVICE_ACTOR
    assert binding["actor_id"] == binding["created_by"] == SERVICE_ACTOR
    assert plan["created_by"] == SERVICE_ACTOR
    assert dispatch["actor_id"] == SERVICE_ACTOR
    for owner_body in (pool, binding, plan, dispatch):
        assert owner_body["metadata"]["requested_by"] == HUMAN_REQUESTER
        assert not _contains_value(owner_body, GOVERNANCE_SUBJECT)
    for registry_id in (ids.baseline_registry_id, ids.registry_id):
        advance = _post_payload(
            transport,
            f"/api/registry/strategy-specs/{registry_id}/advance",
        )
        # Registry binds the actor from the verified transport subject; the
        # body names only the Governance decision and the CAS base.
        assert "approver" not in advance
        assert "actor_id" not in advance
        assert advance["approval_decision_id"]
        assert not _contains_value(advance, HUMAN_REQUESTER)
        assert not _contains_value(advance, GOVERNANCE_SUBJECT)


def test_governance_actor_defaults_to_the_service_actor() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport(governance_subject=SERVICE_ACTOR)
    coordinator = _coordinator(store, transport, _schedule_receipt, actor_id=SERVICE_ACTOR)

    assert coordinator.governance_actor_id == SERVICE_ACTOR
    assert coordinator.coordinate(record).state == "provisioning"
    for _path, body in _governance_posts(transport):
        assert body.get("owner_user_id", body.get("actor_id")) == SERVICE_ACTOR

    with pytest.raises(ValueError):
        PersonaProvisioningCoordinator(
            store=store,
            transport=transport,
            schedule_registrar=_schedule_receipt,
            lease_owner="w",
            governance_actor_id="   ",
        )


def test_proposal_owner_that_is_not_the_verified_subject_is_denied_without_persistence() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    # The transport authenticates as the verified subject; the coordinator is
    # still configured with the retired shared identity.
    transport = FakeOwnerTransport(governance_subject=GOVERNANCE_SUBJECT)
    schedule_called = False

    def registrar(*_args):
        nonlocal schedule_called
        schedule_called = True
        raise AssertionError("denied proposal must never reach scheduling")

    result = _coordinator(store, transport, registrar, actor_id=SERVICE_ACTOR).coordinate(record)

    assert result.state == "failed"
    assert result.error["failed_step"] == "baseline_approval_proposed"
    assert "Proposal owner" in result.error["terminal_reason"]
    assert result.compensation is None
    assert schedule_called is False
    proposal_path = f"/api/governance/approvals/{ids.baseline_approval_decision_id}"
    assert ("governance", proposal_path) not in transport.objects
    assert [path for path, _body in _governance_posts(transport)] == [
        "/api/governance/approvals"
    ]
    assert "persona_capital_binding_created" not in result.references
    assert "deployment_plan" not in result.references


def test_proposal_and_decision_bind_exact_owner_checksum_and_finite_expiry() -> None:
    _store, record, transport, _result = _separate_principal_run()
    ids = deterministic_provisioning_ids(record)

    for decision_id, kind, registry_id in _approval_paths(ids):
        owner_entry = _owner_entry(transport, kind, registry_id)
        if kind == "strategy-specs":
            # Registry derives the durable checksum from the inline spec it
            # stored; the proposal must cite that value, not a client hash.
            assert owner_entry["checksum"] == _strategy_spec_checksum(
                owner_entry["metadata"]["strategy_spec"]
            )
        proposal_body = _post_payload(
            transport,
            "/api/governance/approvals",
            identity=("decision_id", decision_id),
        )
        review_body = _post_payload(transport, f"/api/governance/approvals/{decision_id}/review")
        decide_body = _post_payload(transport, f"/api/governance/approvals/{decision_id}/decide")

        assert proposal_body["candidate_digest"] == owner_entry["checksum"]
        assert decide_body["candidate_digest"] == owner_entry["checksum"]
        expected_expiry = _plus_24h(owner_entry["created_at"])
        assert proposal_body["expires_at"] == decide_body["expires_at"] == expected_expiry
        assert proposal_body["target_id"] == registry_id
        assert proposal_body["target_version"] == owner_entry["version"]
        assert proposal_body["tenant_id"] == record.tenant_id

        decision = _replay_domain_decision(proposal_body, review_body, decide_body)
        persisted = transport.objects[("governance", f"/api/governance/approvals/{decision_id}")]
        assert persisted["candidate_digest"] == decision.candidate_digest == owner_entry["checksum"]
        assert persisted["expires_at"] == decision.expires_at == expected_expiry
        created_at = datetime.fromisoformat(decision.created_at.replace("Z", "+00:00"))
        expiry = datetime.fromisoformat(decision.expires_at.replace("Z", "+00:00"))
        assert timedelta(0) < expiry - created_at <= APPROVAL_TTL
        assert APPROVAL_TTL == timedelta(hours=24)

        # Registry's approval verifier: exact tenant/target/version/checksum
        # and a current expiry, exactly as RegistryService.advance_artifact_state
        # asks the shared reader.
        expected = {
            "tenant_id": record.tenant_id,
            "target_type": "registry_entry",
            "target_id": registry_id,
            "target_version": owner_entry["version"],
            "candidate_digest": owner_entry["checksum"],
        }
        evidence = ApprovalEvidence.model_validate(decision.to_dict())
        evidence.require_valid(expected=expected)
        with pytest.raises(ApprovalInvalid):
            evidence.require_valid(expected={**expected, "candidate_digest": "sha256:other"})
        with pytest.raises(ApprovalInvalid):
            evidence.require_valid(expected=expected, now=expiry)
        with pytest.raises(ApprovalInvalid):
            evidence.require_valid(expected=expected, now=expiry + timedelta(seconds=1))
        evidence.require_valid(expected=expected, now=expiry - timedelta(seconds=1))


def test_forward_strategy_spec_cites_exact_baseline_parent() -> None:
    _store, record, transport, _result = _separate_principal_run()
    ids = deterministic_provisioning_ids(record)
    baseline_entry = RegistryEntry.from_dict(
        _owner_entry(transport, "strategy-specs", ids.baseline_registry_id)
    )
    forward = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.registry_id),
    )
    baseline = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.baseline_registry_id),
    )

    assert forward["lineage"]["parent_registry_ids"] == [ids.baseline_registry_id]
    assert forward["version"] == ids.version == "1.0.0"
    assert baseline["version"] == ids.baseline_version == "0.0.1"
    assert forward["strategy_spec"]["metadata"]["capital_scale_pct"] == 0.0
    assert forward["strategy_spec"]["execution_profile"]["execution_mode_hint"] == "paper"
    assert forward["metadata"]["capital_scale_pct"] == 0.0

    # The initial revision may not claim a parent; the forward revision must
    # name its exact existing parent, at the latest version, and step forward.
    _check_strategy_spec_version_lineage(
        [], ids.strategy_id, baseline["version"], Lineage.from_dict(baseline["lineage"])
    )
    _check_strategy_spec_version_lineage(
        [baseline_entry],
        ids.strategy_id,
        forward["version"],
        Lineage.from_dict(forward["lineage"]),
        base_checksum=baseline_entry.checksum,
    )
    orphan = deepcopy(forward["lineage"])
    orphan.pop("parent_registry_ids")
    with pytest.raises(RegistryError):
        _check_strategy_spec_version_lineage(
            [baseline_entry], ids.strategy_id, forward["version"], Lineage.from_dict(orphan)
        )
    with pytest.raises(RegistryError):
        _check_strategy_spec_version_lineage(
            [baseline_entry],
            ids.strategy_id,
            forward["version"],
            Lineage.from_dict({**forward["lineage"], "parent_registry_ids": ["reg-unknown"]}),
        )
    with pytest.raises(RegistryConflictError):
        _check_strategy_spec_version_lineage(
            [baseline_entry],
            ids.strategy_id,
            forward["version"],
            Lineage.from_dict(forward["lineage"]),
            base_checksum="sha256:stale",
        )
    with pytest.raises(RegistryError):
        _check_strategy_spec_version_lineage(
            [], ids.strategy_id, forward["version"], Lineage.from_dict(forward["lineage"])
        )


def _fail_then_prepare_retry(
    failure_path_suffix: str,
) -> tuple[Any, Any, FakeOwnerTransport, Any]:
    """Run to a safe early failure at the baseline approval, keeping owner state."""

    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    failure_path = f"/api/governance/approvals/{ids.baseline_approval_decision_id}/{failure_path_suffix}"
    transport = FakeOwnerTransport(
        mutation_failure={failure_path},
        governance_subject=GOVERNANCE_SUBJECT,
    )
    first = _coordinator(
        store,
        transport,
        _schedule_receipt,
        actor_id=SERVICE_ACTOR,
        governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(record)
    assert first.state == "failed"
    assert first.compensation is None
    transport.mutation_failure.remove(failure_path)
    return store, record, transport, ids


def _retry(store, record, transport) -> Any:
    return PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=_schedule_receipt,
        lease_owner="retry-worker",
        actor_id=SERVICE_ACTOR,
        governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))


def test_retry_after_lost_decide_composes_identical_governance_bodies() -> None:
    store, record, transport, ids = _fail_then_prepare_retry("decide")
    decide_path = f"/api/governance/approvals/{ids.baseline_approval_decision_id}/decide"
    first_decide = deepcopy(_post_payload(transport, decide_path))
    first_proposal = deepcopy(
        _post_payload(
            transport,
            "/api/governance/approvals",
            identity=("decision_id", ids.baseline_approval_decision_id),
        )
    )

    retried = _retry(store, record, transport)

    assert retried.state == "provisioning"
    decide_bodies = [body for path, body in _governance_posts(transport) if path == decide_path]
    assert decide_bodies == [first_decide, first_decide]
    proposals = [
        body
        for path, body in _governance_posts(transport)
        if path == "/api/governance/approvals"
        and body["decision_id"] == ids.baseline_approval_decision_id
    ]
    assert proposals == [first_proposal]


@pytest.mark.parametrize(
    ("failure_suffix", "tampered_field", "tampered_value"),
    [
        ("decide", "candidate_digest", "sha256:" + "f" * 64),
        ("decide", "owner_user_id", HUMAN_REQUESTER),
        ("review", "expires_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_persisted_proposal_that_binds_other_values_is_denied_without_mutation(
    failure_suffix: str,
    tampered_field: str,
    tampered_value: str,
) -> None:
    store, record, transport, ids = _fail_then_prepare_retry(failure_suffix)
    decision_path = f"/api/governance/approvals/{ids.baseline_approval_decision_id}"
    # Another writer re-bound the same decision ID to a different digest,
    # owner or expiry.  This coordination must neither advance nor renew it.
    transport.objects[("governance", decision_path)][tampered_field] = tampered_value
    mutations_before = transport.mutations.copy()

    retried = _retry(store, record, transport)

    assert retried.state == "failed"
    assert retried.error["failed_step"] == "baseline_approval_proposed"
    assert retried.compensation is None
    assert transport.mutations == mutations_before
    assert transport.objects[("governance", decision_path)][tampered_field] == tampered_value
    assert "persona_capital_binding_created" not in retried.references


def test_decided_approval_replay_with_conflicting_digest_is_denied() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    advance_path = f"/api/registry/strategy-specs/{ids.baseline_registry_id}/advance"
    transport = FakeOwnerTransport(
        mutation_failure={advance_path},
        governance_subject=GOVERNANCE_SUBJECT,
    )
    first = _coordinator(
        store,
        transport,
        _schedule_receipt,
        actor_id=SERVICE_ACTOR,
        governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(record)
    assert first.error["failed_step"] == "baseline_strategy_spec_approved"
    decision_path = f"/api/governance/approvals/{ids.baseline_approval_decision_id}"
    decided = transport.objects[("governance", decision_path)]
    assert decided["decision_state"] == "decided"
    decided["candidate_digest"] = "sha256:" + "e" * 64
    transport.mutation_failure.remove(advance_path)
    mutations_before = transport.mutations.copy()

    retried = _retry(store, record, transport)

    assert retried.state == "failed"
    assert retried.error["failed_step"] == "baseline_approval_proposed"
    assert "checksum" in retried.error["terminal_reason"]
    assert transport.mutations == mutations_before
    assert transport.mutations[("registry", advance_path)] == 1


def test_expired_proposal_fails_closed_without_renewal() -> None:
    store, record, transport, ids = _fail_then_prepare_retry("decide")
    decision_path = f"/api/governance/approvals/{ids.baseline_approval_decision_id}"
    entry_path = f"/api/registry/strategy-specs/{ids.baseline_registry_id}"
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Owner state as it would be read back more than a day later: the
    # candidate and its proposal are unchanged, the bound expiry has passed.
    entry = transport.objects[("registry", entry_path)]["entry"]
    entry["created_at"] = entry["updated_at"] = stale
    proposal = transport.objects[("governance", decision_path)]
    proposal["created_at"] = stale
    proposal["expires_at"] = _plus_24h(stale)
    mutations_before = transport.mutations.copy()

    retried = _retry(store, record, transport)

    assert retried.state == "failed"
    assert retried.error["failed_step"] == "baseline_approval_decided"
    assert "expired" in retried.error["terminal_reason"]
    assert "not renewed" in retried.error["terminal_reason"]
    assert retried.compensation is None
    # Neither a decide nor a fresh proposal was issued: no ungoverned renewal.
    assert transport.mutations == mutations_before
    assert proposal["decision_state"] == "under_review"
    assert proposal["expires_at"] == _plus_24h(stale)


def test_decide_never_exceeds_24h_after_persisted_approval_creation() -> None:
    store, record, transport, ids = _fail_then_prepare_retry("decide")
    decision_path = f"/api/governance/approvals/{ids.baseline_approval_decision_id}"
    proposal = transport.objects[("governance", decision_path)]
    # A proposal whose persisted creation time is older than its expiry bound
    # allows must not be decided with that expiry.
    proposal["created_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    mutations_before = transport.mutations.copy()

    retried = _retry(store, record, transport)

    assert retried.state == "failed"
    assert retried.error["failed_step"] == "baseline_approval_decided"
    assert "bound" in retried.error["terminal_reason"]
    assert transport.mutations == mutations_before


def test_governance_wire_models_forbid_transport_fields_in_bodies() -> None:
    _store, record, transport, _result = _separate_principal_run()
    ids = deterministic_provisioning_ids(record)
    proposal_body = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.approval_decision_id),
    )
    decide_body = _post_payload(
        transport,
        f"/api/governance/approvals/{ids.approval_decision_id}/decide",
    )

    ProposeApprovalRequest(**proposal_body)
    DecideRequest(**decide_body)
    for body, model in ((proposal_body, ProposeApprovalRequest), (decide_body, DecideRequest)):
        assert "idempotency_key" not in body
        assert "Idempotency-Key" not in body
        with pytest.raises(ValueError):
            model(**{**body, "idempotency_key": "not-a-body-field"})


# ---------------------------------------------------------------------------
# Registry advance caller contract against the REAL Registry facade
# ---------------------------------------------------------------------------
#
# Registry calls below go through the real FastAPI routes, strict JWT
# verification, ``RegistryService.advance_artifact_state`` and the in-memory
# owner store's CAS/command-receipt logic.  Governance stays the strict
# in-memory double; its persisted decisions are surfaced to Registry through
# the same ``ApprovalEvidence`` reader hook Registry uses in production.

import time  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from services.governance.test_approval_authority import configure_registry_unit_auth  # noqa: E402
from services.registry.service import AdvanceRequest, app as registry_app  # noqa: E402
from services.registry.storage import reset_store  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402

REGISTRY_SUBJECT = "control-plane-bff"
DEV_PAPER_SUBJECT = "pantheon-dev-paper-provisioner"


def _registry_headers(tenant_id: str, *, subject: str = REGISTRY_SUBJECT) -> dict[str, str]:
    token = encode_jwt_hs256(
        dict(
            sub=subject,
            tenant=tenant_id,
            roles=["operator"],
            iss="registry-unit",
            aud="registry-unit",
            exp=time.time() + 900,
        ),
        secret="synthetic-unit-key",
    )
    return {"Authorization": "Bearer " + token}


class _GovernanceEvidenceReader:
    """Registry's exact-ID approval read, served from the strict Governance double."""

    def __init__(self, transport: FakeOwnerTransport) -> None:
        self.transport = transport

    def get(self, decision_id: str) -> ApprovalEvidence:
        body = self.transport.objects.get(("governance", f"/api/governance/approvals/{decision_id}"))
        if body is None:
            raise ApprovalInvalid("Governance denied exact decision read")
        evidence = ApprovalEvidence.model_validate(body)
        if evidence.decision_id != decision_id:
            raise ApprovalInvalid("Governance exact decision ID mismatch")
        return evidence

    def verify(self, decision_id: str, *, expected, now=None, **kwargs) -> ApprovalEvidence:
        return self.get(decision_id).require_valid(expected=expected, now=now, **kwargs)


class RealRegistryTransport(FakeOwnerTransport):
    """Registry over the real HTTP app as the verified BFF subject; other owners in-memory."""

    def __init__(self, client: TestClient, headers: dict[str, str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.client = client
        self.headers = headers

    def get(self, owner: str, path: str):
        if owner != "registry":
            return super().get(owner, path)
        self.calls.append(("GET", owner, path, None))
        response = self.client.get(path, headers=self.headers)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError(f"registry GET {path} -> HTTP {response.status_code}: {response.text}")
        return response.json()

    def post(self, owner: str, path: str, payload):
        if owner != "registry":
            return super().post(owner, path, payload)
        body = deepcopy(dict(payload))
        self.calls.append(("POST", owner, path, body))
        self.mutations[(owner, path)] += 1
        if path in self.mutation_failure:
            raise ConnectionError(f"owner rejected {path}")
        response = self.client.post(path, json=body, headers=self.headers)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP Error {response.status_code}: {response.text}")
        if path in self.response_loss:
            self.response_loss.remove(path)
            raise ConnectionError(f"response lost for {path}")
        return response.json()

    def _owner_registry_entry(self, registry_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"/api/registry/strategy-artifacts/{registry_id}", headers=self.headers
        )
        assert response.status_code == 200, response.text
        entry = response.json()["entry"]
        assert entry["artifact_state"] == "approved"
        return entry


@pytest.fixture
def real_registry(monkeypatch):
    configure_registry_unit_auth(monkeypatch)
    # The registry never defaults to its in-memory double; opt in explicitly.
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    reset_store()
    holder: dict[str, Any] = {}

    def make_transport(**kwargs: Any) -> RealRegistryTransport:
        transport = RealRegistryTransport(
            TestClient(registry_app), _registry_headers("tenant-a"), **kwargs
        )
        holder["reader"] = _GovernanceEvidenceReader(transport)
        return transport

    with patch(
        "services.governance.approval_authority.configured_approval_reader",
        side_effect=lambda *_args, **_kwargs: holder["reader"],
    ):
        yield make_transport
    reset_store()


def _registry_entry_http(transport: RealRegistryTransport, kind: str, registry_id: str) -> dict[str, Any]:
    response = transport.client.get(f"/api/registry/{kind}/{registry_id}", headers=transport.headers)
    assert response.status_code == 200, response.text
    return response.json()["entry"]


def _advance_bodies(transport: FakeOwnerTransport, path: str) -> list[dict[str, Any]]:
    return [call[3] for call in transport.calls if call[0] == "POST" and call[2] == path]


def test_real_registry_admits_coordinator_advance_bound_to_transport_subject(real_registry) -> None:
    transport = real_registry(governance_subject=GOVERNANCE_SUBJECT)
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)

    result = _coordinator(
        store, transport, _schedule_receipt,
        actor_id=SERVICE_ACTOR, governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(record)

    assert result.state == "provisioning", result.error
    assert result.current_step == "schedule_registered"
    for kind, registry_id, decision_id in (
        ("strategy-specs", ids.baseline_registry_id, ids.baseline_approval_decision_id),
        ("strategy-artifacts", ids.baseline_strategy_artifact_id,
         ids.baseline_strategy_artifact_approval_decision_id),
        ("strategy-specs", ids.registry_id, ids.approval_decision_id),
        ("strategy-artifacts", ids.strategy_artifact_id, ids.strategy_artifact_approval_decision_id),
    ):
        advance_path = f"/api/registry/{kind}/{registry_id}/advance"
        bodies = _advance_bodies(transport, advance_path)
        assert len(bodies) == 1
        body = AdvanceRequest(**bodies[0])
        assert body.approver is None
        assert body.approval_decision_id == decision_id
        assert body.expected_artifact_state.value == "candidate"
        # The CAS base is the exact authoritative candidate row read just
        # before the transition, not a guessed version.
        candidate_readback = next(
            call for call in transport.calls
            if call[0] == "GET" and call[2] == f"/api/registry/{kind}/{registry_id}"
        )
        assert candidate_readback is not None
        entry = _registry_entry_http(transport, kind, registry_id)
        assert entry["artifact_state"] == "approved"
        assert body.expected_version == entry["version"]
        assert body.expected_updated_at < entry["updated_at"] or (
            body.expected_updated_at == entry["updated_at"]
        )
        assert body.command_key.startswith(
            f"persona-provisioning:{ids.token}:advance:{registry_id}:approved:"
        )
        # Registry bound the approver to the Governance decision actor and the
        # write to the verified transport subject; the human never appears.
        assert entry["approval_decision_id"] == decision_id
        assert entry["approver"] == GOVERNANCE_SUBJECT
        assert entry["approval_evidence"]["candidate_digest"] == entry["checksum"]
        assert entry["last_actor"]["actor_id"] == REGISTRY_SUBJECT
        assert entry["last_actor"]["tenant"] == record.tenant_id
        assert entry["owner_tenant"] == record.tenant_id
        assert entry["metadata"]["requested_by"] == HUMAN_REQUESTER


def test_real_registry_replays_same_command_and_rejects_divergent_or_retired_bodies(real_registry) -> None:
    transport = real_registry(governance_subject=GOVERNANCE_SUBJECT)
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    assert _coordinator(
        store, transport, _schedule_receipt,
        actor_id=SERVICE_ACTOR, governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(record).state == "provisioning"
    advance_path = f"/api/registry/strategy-specs/{ids.baseline_registry_id}/advance"
    body = _advance_bodies(transport, advance_path)[0]
    approved = _registry_entry_http(transport, "strategy-specs", ids.baseline_registry_id)

    # Identical command under the same key: the committed row is replayed,
    # nothing is re-transitioned.
    replay = transport.client.post(advance_path, json=body, headers=transport.headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["entry"]["approved_at"] == approved["approved_at"]
    assert _registry_entry_http(transport, "strategy-specs", ids.baseline_registry_id) == approved

    # Same key, different CAS base: divergent replay is a conflict.
    divergent = transport.client.post(
        advance_path, json={**body, "expected_updated_at": "2000-01-01T00:00:00Z"},
        headers=transport.headers,
    )
    assert divergent.status_code == 409, divergent.text

    # A caller-supplied approver is retired by the owner.
    retired = transport.client.post(
        advance_path, json={**body, "command_key": body["command_key"] + ":x", "approver": SERVICE_ACTOR},
        headers=transport.headers,
    )
    assert retired.status_code == 400, retired.text
    assert "approver" in retired.text

    # The transport subject is verified: another subject's token cannot advance
    # as the BFF, and a token for another tenant cannot touch this entry.
    foreign = transport.client.post(
        advance_path, json={**body, "command_key": body["command_key"] + ":y"},
        headers=_registry_headers("tenant-b"),
    )
    assert foreign.status_code == 403, foreign.text
    assert _registry_entry_http(transport, "strategy-specs", ids.baseline_registry_id) == approved


def test_real_registry_lost_advance_response_converges_without_second_transition(real_registry) -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    advance_path = f"/api/registry/strategy-specs/{ids.baseline_registry_id}/advance"
    transport = real_registry(governance_subject=GOVERNANCE_SUBJECT, response_loss={advance_path})

    first = _coordinator(
        store, transport, _schedule_receipt,
        actor_id=SERVICE_ACTOR, governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(record)
    assert first.state == "provisioning", first.error
    assert transport.mutations[("registry", advance_path)] == 1
    mutations_after_first = transport.mutations.copy()

    restarted = _retry(store, record, transport)

    assert restarted.state == "provisioning"
    assert transport.mutations == mutations_after_first


def test_real_registry_precommit_advance_failure_retries_with_identical_command(real_registry) -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    advance_path = f"/api/registry/strategy-specs/{ids.baseline_registry_id}/advance"
    transport = real_registry(governance_subject=GOVERNANCE_SUBJECT, mutation_failure={advance_path})

    first = _coordinator(
        store, transport, _schedule_receipt,
        actor_id=SERVICE_ACTOR, governance_actor_id=GOVERNANCE_SUBJECT,
    ).coordinate(record)
    assert first.state == "failed"
    assert first.error["failed_step"] == "baseline_strategy_spec_approved"
    assert _registry_entry_http(transport, "strategy-specs", ids.baseline_registry_id)[
        "artifact_state"
    ] == "candidate"
    transport.mutation_failure.remove(advance_path)

    retried = _retry(store, record, transport)

    assert retried.state == "provisioning", retried.error
    bodies = _advance_bodies(transport, advance_path)
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert _registry_entry_http(transport, "strategy-specs", ids.baseline_registry_id)[
        "artifact_state"
    ] == "approved"


def _scoped_paper_run(real_registry, monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "true")
    transport = real_registry(governance_subject=DEV_PAPER_SUBJECT)
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    result = _coordinator(
        store, transport, _schedule_receipt,
        actor_id=SERVICE_ACTOR, governance_actor_id=DEV_PAPER_SUBJECT,
    ).coordinate(record)
    return transport, ids, result


def test_scoped_paper_approval_admits_coordinator_spec_through_real_registry(real_registry, monkeypatch) -> None:
    from services.governance.paper_approval_scope import DEV_PAPER_AUTHORIZATION_SCOPE

    transport, ids, _result = _scoped_paper_run(real_registry, monkeypatch)

    entry = _registry_entry_http(transport, "strategy-specs", ids.baseline_registry_id)
    assert entry["artifact_state"] == "approved"
    assert entry["approver"] == DEV_PAPER_SUBJECT
    assert entry["approval_evidence"]["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
    assert entry["approval_evidence"]["candidate_digest"] == entry["checksum"]
    assert entry["last_actor"]["actor_id"] == REGISTRY_SUBJECT
    persisted = transport.objects[
        ("governance", f"/api/governance/approvals/{ids.baseline_approval_decision_id}")
    ]
    assert persisted["owner_user_id"] == DEV_PAPER_SUBJECT
    assert persisted["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE


def test_scoped_paper_approval_admits_coordinator_bundles_through_real_registry(real_registry, monkeypatch) -> None:
    transport, ids, result = _scoped_paper_run(real_registry, monkeypatch)

    assert result.state == "provisioning", result.error
    bundle = _registry_entry_http(transport, "strategy-artifacts", ids.strategy_artifact_id)
    assert bundle["artifact_state"] == "approved"


def test_scoped_paper_bundle_admission_never_fabricates_binding(real_registry, monkeypatch) -> None:
    transport, ids, result = _scoped_paper_run(real_registry, monkeypatch)
    assert result.state == "provisioning", result.error
    assert result.compensation is None
    bundle = _registry_entry_http(transport, "strategy-artifacts", ids.baseline_strategy_artifact_id)
    assert bundle["artifact_state"] == "approved"
    assert "binding_intent" not in bundle["metadata"]["strategy_artifact"]
    assert "runtime_binding_id" not in result.references
    assert "runtime_id" not in result.references
