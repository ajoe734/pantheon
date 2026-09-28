"""Regression coverage for BFF-EVIDENCE-REDACTION-FAIL-CLOSED-001.

``models.redact_evidence_refs`` had two fail-open paths:

1. ``capabilities=None`` returned every ref unredacted with a redacted
   count of zero (identity-with-no-capabilities was silently treated as
   full visibility).
2. A ref whose kind could not be resolved (``required_capability`` empty)
   was appended unchanged instead of withheld.

This file:

- exercises the base function directly for both fixed paths, plus the
  "full-capability identity still sees a known-kind ref" and "known kind,
  insufficient capability" cases;
- exercises one real-router request per direct-caller surface (the 8
  direct callers of ``redact_evidence_refs`` audited for this task:
  ``research/service.py`` x3 -- KW03 evidence list/detail/linked-decisions
  -- ``personas/service.py`` PM12 quarterly evidence,
  ``assistant/management_service.py`` NL-ask evidence grounding,
  ``management_read_models/service.py`` management evidence list, and
  ``governance/service.py`` x2 -- consult-memo and mutation-review
  evidence) with a low-capability identity, a full-capability identity,
  and a capability-resolution failure.

Identities are built through ``OperatorIdentity`` (never ``SimpleNamespace``)
via the real ``auth.policy`` stub-token identity extractor, so these are the
same production identity/capability objects the routers use at runtime.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import (
    OperatorIdentity,
    fail_closed_redacted_refs,
    redact_evidence_refs,
)


# ---------------------------------------------------------------------------
# Shared stub-auth helper (mirrors test_control_loops_evidence_redaction_sweep.py)
# ---------------------------------------------------------------------------

@contextmanager
def _stub_auth_env() -> Iterator[None]:
    tracked = {
        "PANTHEON_BFF_AUTH_STUB": os.environ.get("PANTHEON_BFF_AUTH_STUB"),
        "PANTHEON_BFF_AUTH_MODE": os.environ.get("PANTHEON_BFF_AUTH_MODE"),
    }
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "1"
    os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"
    try:
        yield
    finally:
        for key, value in tracked.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


LOW_CAPABILITY_AUTH = "Bearer evd-fc-operator:operator"
FULL_CAPABILITY_AUTH = "Bearer evd-fc-admin:admin"


# ===========================================================================
# 1. Base function coverage
# ===========================================================================

def test_base_function_capabilities_none_fails_closed() -> None:
    """Fixed fail-open path 1: capabilities=None must gate, not pass through."""
    identity = OperatorIdentity(operator_id="op-base-1", roles=["operator"])
    refs = [{"ref_id": "audit-ref-1", "evidence_type": "audit"}]

    processed, redacted_count = redact_evidence_refs(identity, refs, capabilities=None)

    assert redacted_count == 1
    assert processed[0]["redacted"] is True
    assert processed[0]["required_capability"] == "audit.read"
    assert processed[0]["reason"] == "insufficient_capability"


def test_base_function_unresolved_kind_fails_closed() -> None:
    """Fixed fail-open path 2: an unresolvable kind must be withheld."""
    identity = OperatorIdentity(operator_id="op-base-2", roles=["admin"])
    refs = [{"ref_id": "mystery-object-9f31", "payload": {"note": "no kind hint anywhere"}}]

    # Even a full-capability identity cannot be shown a ref whose kind (and
    # therefore required_capability) cannot be verified.
    processed, redacted_count = redact_evidence_refs(identity, refs, capabilities=[])

    assert redacted_count == 1
    entry = processed[0]
    assert entry["redacted"] is True
    assert entry["ref_id"] == "mystery-object-9f31"
    assert entry["kind"] is None
    assert entry["required_capability"] == "unknown"
    assert entry["reason"] == "unresolved_evidence_kind"


def test_base_function_full_capability_identity_still_sees_known_kind_ref() -> None:
    """No over-redaction regression: a resolvable kind passes for full capability."""
    identity = OperatorIdentity(operator_id="op-base-3", roles=["admin"])
    refs = [{"ref_id": "audit-ref-2", "evidence_type": "audit"}]

    processed, redacted_count = redact_evidence_refs(
        identity, refs, capabilities=["audit.read"]
    )

    assert redacted_count == 0
    assert processed == refs


def test_base_function_known_kind_insufficient_capability_is_redacted() -> None:
    identity = OperatorIdentity(operator_id="op-base-4", roles=["operator"])
    refs = [{"ref_id": "audit-ref-3", "evidence_type": "audit"}]

    processed, redacted_count = redact_evidence_refs(identity, refs, capabilities=[])

    assert redacted_count == 1
    entry = processed[0]
    assert entry["redacted"] is True
    assert entry["kind"] == "audit"
    assert entry["required_capability"] == "audit.read"
    assert entry["reason"] == "insufficient_capability"


def test_base_function_string_ref_capabilities_none_fails_closed() -> None:
    """Plain-string refs (OODA-style) must also fail closed on capabilities=None."""
    identity = OperatorIdentity(operator_id="op-base-5", roles=["operator"])
    refs = ["paper-broker://logged-only-order/review-order"]

    processed, redacted_count = redact_evidence_refs(identity, refs, capabilities=None)

    assert redacted_count == 1
    entry = processed[0]
    assert entry["redacted"] is True
    assert entry["ref_id"] == "paper-broker://logged-only-order/review-order"
    assert entry["required_capability"] == "audit.read"


# ===========================================================================
# 2. Caller 1-3: research/service.py (KW03 evidence list / detail / linked
#    decisions) via the real research router.
# ===========================================================================

def _kw03_seeded_client():
    from services.control_plane.bff.tests.knowledge_read_port_fixtures import (
        create_seeded_knowledge_read_ports,
    )
    from services.control_plane.bff.research.router import create_research_router

    ports = create_seeded_knowledge_read_ports()
    app = FastAPI()
    app.include_router(
        create_research_router(
            read_surface=ports,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-09-28T00:00:00Z",
            get_capabilities=auth_policy.capabilities_for_identity,
        )
    )
    return TestClient(app)


def test_kw03_research_evidence_list_and_detail_redact_for_low_capability() -> None:
    with _stub_auth_env():
        client = _kw03_seeded_client()

        listing = client.get(
            "/api/v1/knowledge/evidence", headers={"Authorization": LOW_CAPABILITY_AUTH}
        )
        assert listing.status_code == 200, listing.text
        payload = listing.json()
        assert payload["evidence_refs"], "seeded evidence list must not be empty"


def test_kw03_research_evidence_list_and_detail_pass_through_for_full_capability() -> None:
    with _stub_auth_env():
        client = _kw03_seeded_client()

        listing = client.get(
            "/api/v1/knowledge/evidence", headers={"Authorization": FULL_CAPABILITY_AUTH}
        )
        assert listing.status_code == 200, listing.text
        payload = listing.json()
        for item in payload["evidence_refs"]:
            assert item.get("redacted") is not True, (
                f"full-capability identity must not have any evidence ref "
                f"withheld, got: {item}"
            )


def test_kw03_research_evidence_list_fails_closed_when_capabilities_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List route's ``redact_evidence_refs`` call site fails closed when the
    capability lookup raises.

    ``ResearchRouterService._get_capabilities_for`` has its own resilience
    layer: an injected ``get_capabilities`` raising falls back to the real
    ``auth.policy.capabilities_for_identity``, then to
    ``identity.capabilities``, before finally returning ``None``. Monkeypatch
    the policy function too so every fallback genuinely fails, matching how
    a real capability-service outage would surface.
    """
    from services.control_plane.bff.tests.knowledge_read_port_fixtures import (
        create_seeded_knowledge_read_ports,
    )
    from services.control_plane.bff.research.router import create_research_router

    def _boom(identity: Any) -> list:
        raise RuntimeError("capability lookup unavailable")

    monkeypatch.setattr(auth_policy, "capabilities_for_identity", _boom)

    ports = create_seeded_knowledge_read_ports()
    app = FastAPI()
    app.include_router(
        create_research_router(
            read_surface=ports,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-09-28T00:00:00Z",
            get_capabilities=_boom,
        )
    )
    with _stub_auth_env():
        client = TestClient(app)
        listing = client.get(
            "/api/v1/knowledge/evidence", headers={"Authorization": FULL_CAPABILITY_AUTH}
        )
        assert listing.status_code == 200, listing.text
        payload = listing.json()
        assert payload["evidence_refs"], "seeded evidence list must not be empty"
        for item in payload["evidence_refs"]:
            assert item.get("redacted") is True, (
                f"a raised capability lookup must fail closed even for an "
                f"admin-shaped token, got: {item}"
            )


_KW03_DETAIL_REF_ID = "evref-c3d4e5f6-a7b8-9012-cdef-012345678901"


def test_kw03_research_evidence_detail_self_and_linked_decisions_low_vs_full_capability() -> None:
    """Exercises the detail route's self-ref redaction (evidence_type computed
    from source_document.source_type=experiment_artifact -> artifact) and its
    linked_decisions redaction (entity_type=memory_entry -> artifact), the
    two other knowledge.py-surfaced ``redact_evidence_refs`` call sites."""
    low_client = _kw03_seeded_client()
    full_client = _kw03_seeded_client()

    with _stub_auth_env():
        low = low_client.get(
            f"/api/v1/knowledge/evidence/{_KW03_DETAIL_REF_ID}",
            headers={"Authorization": "Bearer evd-fc-viewer:viewer"},
        )
        full = full_client.get(
            f"/api/v1/knowledge/evidence/{_KW03_DETAIL_REF_ID}",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )

    assert low.status_code == 200, low.text
    assert full.status_code == 200, full.text
    low_payload = low.json()
    full_payload = full.json()

    # viewer lacks artifact.read: self-ref detail is redacted.
    assert low_payload.get("redacted") is True
    assert low_payload["required_capability"] == "artifact.read"
    # admin has artifact.read: full detail (including linked_decisions) is visible.
    assert full_payload.get("redacted") is not True
    assert full_payload["linked_decisions"][0]["entity_type"] == "memory_entry"


# ===========================================================================
# 3. Caller 4: personas/service.py PM12 quarterly evidence via the real
#    persona router.
# ===========================================================================

def _pm12_evidence_counts(auth_header: str) -> tuple[int, int]:
    """Returns (total_evidence_refs, redacted_count) from the real PM12 route."""
    import services.control_plane.bff.tests.test_bff_pm12_persona_league as pm12_tests

    with tempfile.TemporaryDirectory() as td:
        client = pm12_tests._fresh_client(td)
        response = client.get(
            "/bff/management/quarterly-ranking",
            headers={"Authorization": auth_header},
            params={"quarter": "2026-Q1", "page_size": 5},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        refs = body["data"]["evidence_refs"]
        redacted = sum(1 for ref in refs if isinstance(ref, dict) and ref.get("redacted") is True)
        return len(refs), redacted


def test_pm12_quarterly_evidence_redacts_for_low_capability() -> None:
    with _stub_auth_env():
        total, redacted = _pm12_evidence_counts("Bearer evd-fc-viewer:viewer")
        assert total > 0
        assert redacted > 0, "a low-capability (viewer) identity must have some evidence withheld"


def test_pm12_quarterly_evidence_passes_through_for_full_capability() -> None:
    with _stub_auth_env():
        total, redacted = _pm12_evidence_counts(FULL_CAPABILITY_AUTH)
        assert total > 0
        assert redacted == 0, "admin must see every evidence ref it could see before this change"


def test_pm12_quarterly_evidence_fails_closed_when_capabilities_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.control_plane.bff.personas.service as personas_service

    def _boom(identity: Any) -> list:
        raise RuntimeError("capability lookup unavailable")

    monkeypatch.setattr(personas_service, "_capabilities_for_identity", _boom)
    with _stub_auth_env():
        total, redacted = _pm12_evidence_counts(FULL_CAPABILITY_AUTH)
        assert total > 0
        assert redacted == total, (
            "a raised capability lookup must fail closed even for an "
            "admin-shaped token"
        )


# ===========================================================================
# 4. Caller 5: assistant/management_service.py NL-ask evidence grounding via
#    the real management router.
# ===========================================================================

@pytest.fixture(autouse=True)
def _management_nl_command_idempotency_default_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mirrors test_bff_b6_management_nl_ask.py's fixture of the same name:
    the durable NL-ask command idempotency store needs a writable per-test
    path since the module default does not exist in the test sandbox."""
    if not os.environ.get("PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH"):
        monkeypatch.setenv(
            "PANTHEON_MANAGEMENT_NL_COMMAND_IDEMPOTENCY_STORE_PATH",
            str(tmp_path / "management-nl-command-idempotency.json"),
        )


def _nl_ask_evidence_client():
    from services.control_plane.bff.tests.rebalance_authority_test_support import (
        management_nl_test_client,
    )
    from services.control_plane.bff.tests.test_bff_b6_management_nl_ask import (
        _B6NlAskTestStore,
    )

    return management_nl_test_client(_B6NlAskTestStore())


@contextmanager
def _nl_ask_seeded_evidence_env() -> Iterator[None]:
    tracked = {"PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE")}
    with tempfile.TemporaryDirectory() as td:
        evidence_store = Path(td) / "evidence_refs.json"
        evidence_store.write_text(
            json.dumps(
                {
                    "evref-fc-alert-001": {
                        "ref_id": "evref-fc-alert-001",
                        "evidence_type": "alert",
                        "link_type": "supporting_evidence",
                        "source_document": {
                            "title": "NL grounding alert",
                            "source_type": "alert",
                            "source_ref": "alert://fail-closed/risk-window",
                            "captured_at": "2026-09-28T10:00:00Z",
                        },
                        "credibility": {"tier": "primary", "verified": True},
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)
        try:
            yield
        finally:
            if tracked["PANTHEON_BFF_EVIDENCE_REF_STORE"] is None:
                os.environ.pop("PANTHEON_BFF_EVIDENCE_REF_STORE", None)
            else:
                os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = tracked["PANTHEON_BFF_EVIDENCE_REF_STORE"]


def _nl_ask_evidence_redacted_count(auth_header: str) -> int:
    with _nl_ask_seeded_evidence_env(), _nl_ask_evidence_client() as client:
        response = client.post(
            "/bff/management/nl/ask",
            json={"question": "Evidence redaction fail-closed check?"},
            headers={"Authorization": auth_header, "Idempotency-Key": f"ik-{auth_header}"},
        )
        assert response.status_code == 202, response.text
        return response.json()["meta"]["redacted_evidence_count"]


def test_nl_ask_evidence_passes_through_for_full_capability() -> None:
    # operator role already has risk.alert.read, so use a lower-capability
    # role (viewer, which lacks it) to demonstrate the low/full contrast.
    with _stub_auth_env():
        assert _nl_ask_evidence_redacted_count(FULL_CAPABILITY_AUTH) == 0


def test_nl_ask_evidence_redacts_for_low_capability() -> None:
    with _stub_auth_env():
        assert _nl_ask_evidence_redacted_count("Bearer evd-fc-viewer:viewer") >= 1


def test_nl_ask_evidence_fails_closed_when_capabilities_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.control_plane.bff.assistant.management_service as assistant_management_service

    def _boom(identity: Any) -> list:
        raise RuntimeError("capability lookup unavailable")

    monkeypatch.setattr(assistant_management_service, "_capabilities_for_identity", _boom)
    with _stub_auth_env():
        assert _nl_ask_evidence_redacted_count(FULL_CAPABILITY_AUTH) >= 1


# ===========================================================================
# 5. Caller 6: management_read_models/service.py management evidence list
#    via the real management router.
# ===========================================================================

def test_management_evidence_list_redacts_for_low_capability_and_passes_for_full() -> None:
    from services.control_plane.bff.tests.test_bff_b3_management_evidence import (
        _evidence_client,
    )

    with _stub_auth_env(), _evidence_client() as client:
        low = client.get(
            "/bff/management/evidence", headers={"Authorization": "Bearer evd-fc-viewer:viewer"}
        )
        full = client.get("/bff/management/evidence", headers={"Authorization": FULL_CAPABILITY_AUTH})

    assert low.status_code == 200, low.text
    assert full.status_code == 200, full.text
    low_items = low.json()["data"]["items"]
    full_items = full.json()["data"]["items"]
    assert low_items, "seeded management evidence must not be empty"
    assert any(item.get("redacted") is True for item in low_items), (
        "a low-capability (viewer) identity must have some evidence withheld"
    )
    assert not any(item.get("redacted") is True for item in full_items), (
        "admin must see every evidence ref it could see before this change"
    )


def test_management_evidence_list_fails_closed_when_capabilities_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.control_plane.bff.tests.test_bff_b3_management_evidence import (
        _evidence_client,
    )
    import services.control_plane.bff.management_read_models.service as mrm_service

    def _boom(identity: Any) -> list:
        raise RuntimeError("capability lookup unavailable")

    monkeypatch.setattr(mrm_service, "_capabilities_for_identity", _boom)
    with _stub_auth_env(), _evidence_client() as client:
        response = client.get(
            "/bff/management/evidence", headers={"Authorization": FULL_CAPABILITY_AUTH}
        )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert items, "seeded management evidence must not be empty"
    assert all(item.get("redacted") is True for item in items), (
        "a raised capability lookup must fail closed even for an admin-shaped token"
    )


# ===========================================================================
# 6. Caller 7: governance/service.py consult_memo_projection via the real
#    governance router.
# ===========================================================================

def _consult_memo_client(*, capabilities_for_identity: Any = None) -> TestClient:
    from services.control_plane.bff.test_cw04_redteam_memo_contract import _MemoReadStore

    store = _MemoReadStore("/tmp/unused-consult-memo-store.json")
    app = FastAPI()
    app.include_router(
        create_governance_router(
            get_read_store=lambda: store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            redact_evidence_refs=redact_evidence_refs,
            capabilities_for_identity=capabilities_for_identity or auth_policy.capabilities_for_identity,
        )
    )
    return TestClient(app)


_CONSULT_MEMO_ID = "memo-rt-20260419-081"


def test_consult_memo_evidence_redacts_for_low_capability_and_passes_for_full() -> None:
    with _stub_auth_env():
        low = _consult_memo_client().get(
            f"/api/v1/consult/memos/{_CONSULT_MEMO_ID}",
            headers={"Authorization": "Bearer evd-fc-viewer:viewer"},
        )
        full = _consult_memo_client().get(
            f"/api/v1/consult/memos/{_CONSULT_MEMO_ID}",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )

    assert low.status_code == 200, low.text
    assert full.status_code == 200, full.text
    low_refs = low.json()["evidence_refs"]
    full_refs = full.json()["evidence_refs"]
    assert low_refs, "seeded consult memo evidence must not be empty"
    assert any(ref.get("redacted") is True for ref in low_refs), (
        "a low-capability (viewer) identity must have some evidence withheld"
    )
    assert not any(ref.get("redacted") is True for ref in full_refs), (
        "admin must see every evidence ref it could see before this change"
    )


def test_consult_memo_evidence_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> list:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        response = _consult_memo_client(capabilities_for_identity=_boom).get(
            f"/api/v1/consult/memos/{_CONSULT_MEMO_ID}",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )
    assert response.status_code == 200, response.text
    refs = response.json()["evidence_refs"]
    assert refs, "seeded consult memo evidence must not be empty"
    assert all(ref.get("redacted") is True for ref in refs), (
        "a raised capability lookup must fail closed even for an admin-shaped token"
    )


# ===========================================================================
# 7. Caller 8: governance/service.py mutation_review_projection via the real
#    governance router.
# ===========================================================================

class _MutationReviewStore:
    """Minimal read-store double exposing only what mutation_review_projection needs."""

    def dataset_source(self, dataset: str) -> str:
        return "service_store"

    def get_evolution_decision_by_id(self, decision_id: str) -> Optional[dict]:
        if decision_id != "evo-fc-001":
            return None
        return {
            "id": "evo-fc-001",
            "decision_id": "evo-fc-001",
            "decision_state": "proposed",
            "action_type": "retrain",
            "risk_level": "low",
            "proposed_changes": {},
            "risk_assessment": {},
            "evidence_refs": [
                {"ref_id": "policy-doc-fc-1", "evidence_type": "policy"},
            ],
        }

    def get_approval_decision(self, decision_id: str) -> Optional[dict]:
        return None

    def get_incident(self, incident_id: str) -> Optional[dict]:
        return None

    def get_postmortem(self, postmortem_id: str) -> Optional[dict]:
        return None


def _mutation_review_client(*, capabilities_for_identity: Any = None) -> TestClient:
    store = _MutationReviewStore()
    app = FastAPI()
    app.include_router(
        create_governance_router(
            get_read_store=lambda: store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            redact_evidence_refs=redact_evidence_refs,
            capabilities_for_identity=capabilities_for_identity or auth_policy.capabilities_for_identity,
        )
    )
    return TestClient(app)


def test_mutation_review_evidence_redacts_for_low_capability_and_passes_for_full() -> None:
    with _stub_auth_env():
        low = _mutation_review_client().get(
            "/api/v1/operator/mutation-review/evo-fc-001",
            headers={"Authorization": LOW_CAPABILITY_AUTH},
        )
        full = _mutation_review_client().get(
            "/api/v1/operator/mutation-review/evo-fc-001",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )

    assert low.status_code == 200, low.text
    assert full.status_code == 200, full.text
    low_refs = low.json()["evidence_refs"]
    full_refs = full.json()["evidence_refs"]
    assert low_refs, "seeded mutation review evidence must not be empty"
    # operator role lacks policy.read.
    assert all(ref.get("redacted") is True for ref in low_refs)
    assert all(ref.get("required_capability") == "policy.read" for ref in low_refs)
    assert not any(ref.get("redacted") is True for ref in full_refs), (
        "admin must see every evidence ref it could see before this change"
    )


def test_mutation_review_evidence_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> list:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        response = _mutation_review_client(capabilities_for_identity=_boom).get(
            "/api/v1/operator/mutation-review/evo-fc-001",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )
    assert response.status_code == 200, response.text
    refs = response.json()["evidence_refs"]
    assert refs, "seeded mutation review evidence must not be empty"
    assert all(ref.get("redacted") is True for ref in refs), (
        "a raised capability lookup must fail closed even for an admin-shaped token"
    )
