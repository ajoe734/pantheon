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
    # The single seeded evidence ref resolves to the "artifact" kind, which
    # the operator role (LOW_CAPABILITY_AUTH elsewhere in this file) already
    # holds; use viewer (lacks artifact.read) so this is a genuine
    # low-capability probe rather than a vacuous nonempty-200 check.
    with _stub_auth_env():
        client = _kw03_seeded_client()

        listing = client.get(
            "/api/v1/knowledge/evidence", headers={"Authorization": "Bearer evd-fc-viewer:viewer"}
        )
        assert listing.status_code == 200, listing.text
        payload = listing.json()
        refs = payload["evidence_refs"]
        assert refs, "seeded evidence list must not be empty"
        withheld = [ref for ref in refs if ref.get("redacted") is True]
        assert withheld, (
            "operator (low-capability) identity must have at least one "
            "evidence ref withheld, not just a nonempty 200"
        )
        for ref in withheld:
            assert ref.get("required_capability"), ref
            assert ref.get("reason"), ref
            assert "source_document" not in ref, (
                f"a withheld ref must not leak source_document payload, got: {ref}"
            )
        assert payload["meta"]["redacted_evidence_count"] == len(withheld)


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


def test_kw03_research_evidence_detail_fails_closed_when_capabilities_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The detail route's self-ref ``redact_evidence_refs`` call site must
    also fail closed when the capability lookup raises, mirroring the list
    route's coverage above."""
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
        detail = client.get(
            f"/api/v1/knowledge/evidence/{_KW03_DETAIL_REF_ID}",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload.get("redacted") is True, (
        f"a raised capability lookup must fail closed even for an "
        f"admin-shaped token, got: {payload}"
    )
    assert "source_document" not in payload


class _UnresolvedKindPort:
    """Minimal read-surface stub exposing a single evidence ref whose kind
    cannot be resolved (no ``evidence_type`` and a ``source_document.source_type``
    absent from ``SOURCE_TYPE_TO_EVIDENCE_KIND``)."""

    REF_ID = "evref-unresolved-kind-0001"

    def get_evidence_ref_detail(self, ref_id: str) -> Optional[dict]:
        if ref_id != self.REF_ID:
            return None
        return {
            "ref_id": self.REF_ID,
            "source_document": {"title": "Untyped internal note", "source_type": "internal"},
            "link_type": "supporting_evidence",
            "credibility": {"tier": "primary", "verified": True},
            "resolved_link": {"href": "/knowledge/notes/note-x", "availability": "available"},
            "linked_object_summary": {},
            "linked_decisions": [],
            "source_note_context": {"note_id": "note-x", "title": "Should not leak"},
            "source_memory_context": {"entry_id": "mem-x", "headline": "Should not leak"},
            "created_at": "2026-09-28T00:00:00Z",
        }


def test_kw03_research_evidence_detail_self_ref_unresolved_kind_fails_closed_low_and_full() -> None:
    """Regression for the [P1] research/service.py:1893-1918 self-ref bypass:
    ``_evidence_detail_payload`` used to only call ``redact_evidence_refs`` when
    ``evidence_kind`` was already truthy (``if evidence_kind:``), so a ref whose
    kind could not be resolved skipped redaction entirely and disclosed
    ``source_document``/``source_note_context``/``source_memory_context``. The
    base redactor is now called unconditionally, so an unresolved kind is
    withheld with ``unresolved_evidence_kind`` for every identity -- including
    a full-capability one, since an unverifiable ``required_capability`` can
    never be proven safe to disclose either way."""
    from services.control_plane.bff.research.router import create_research_router

    port = _UnresolvedKindPort()
    app = FastAPI()
    app.include_router(
        create_research_router(
            read_surface=port,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-09-28T00:00:00Z",
            get_capabilities=auth_policy.capabilities_for_identity,
        )
    )
    with _stub_auth_env():
        client = TestClient(app)
        low = client.get(
            f"/api/v1/knowledge/evidence/{port.REF_ID}",
            headers={"Authorization": LOW_CAPABILITY_AUTH},
        )
        full = client.get(
            f"/api/v1/knowledge/evidence/{port.REF_ID}",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )

    for label, response in (("low", low), ("full", full)):
        assert response.status_code == 200, f"{label}: {response.text}"
        payload = response.json()
        assert payload.get("redacted") is True, f"{label}: {payload}"
        assert payload.get("required_capability") == "unknown", f"{label}: {payload}"
        assert payload.get("reason") == "unresolved_evidence_kind", f"{label}: {payload}"
        assert "source_document" not in payload, f"{label}: {payload}"
        assert "source_note_context" not in payload, f"{label}: {payload}"
        assert "source_memory_context" not in payload, f"{label}: {payload}"
        assert "linked_decisions" not in payload, f"{label}: {payload}"


class _AliasKindPort:
    """Read-surface stub exposing a single evidence ref whose kind is only
    resolvable via ``source_document.source_type`` through the canonical
    ``SOURCE_TYPE_TO_EVIDENCE_KIND``/``URI_SCHEME_TO_EVIDENCE_KIND`` alias
    tables -- no ``evidence_type`` field set, matching the independent
    review's exact repro (ref_id=evref-001,
    source_document={source_type: telemetry, ...}, linked_decisions=[])."""

    def __init__(self, ref_id: str, source_type: str) -> None:
        self.ref_id = ref_id
        self._ref = {
            "ref_id": ref_id,
            "source_document": {"source_type": source_type, "title": f"{source_type} snapshot"},
            "link_type": "supporting_evidence",
            "credibility": {"tier": "primary", "verified": True},
            "resolved_link": {"href": f"/evidence/{ref_id}", "availability": "available"},
            "linked_object_summary": {},
            "linked_decisions": [],
            "source_note_context": None,
            "source_memory_context": None,
            "created_at": "2026-09-28T00:00:00Z",
        }

    def list_evidence_refs(self, **kwargs: Any) -> list:
        return [dict(self._ref)]

    def get_evidence_ref_detail(self, ref_id: str) -> Optional[dict]:
        if ref_id != self.ref_id:
            return None
        return dict(self._ref)

    def dataset_source(self, dataset: str) -> str:
        return "service_backend" if dataset == "evidence_refs" else "missing"


def _alias_kind_client(port: "_AliasKindPort") -> TestClient:
    from services.control_plane.bff.research.router import create_research_router

    app = FastAPI()
    app.include_router(
        create_research_router(
            read_surface=port,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-09-28T00:00:00Z",
            get_capabilities=auth_policy.capabilities_for_identity,
        )
    )
    return TestClient(app)


# (source_type, required_capability); required_capability=None marks the
# genuinely-unknown case, which must stay withheld even for a full-capability
# identity since ``unresolved_evidence_kind`` can never be proven safe.
_ALIAS_KIND_CASES = [
    ("postmortem", "postmortem.read"),  # SOURCE_TYPE_TO_EVIDENCE_KIND
    ("audit_log", "audit.read"),  # SOURCE_TYPE_TO_EVIDENCE_KIND
    ("strategy_spec", "strategy.view"),  # SOURCE_TYPE_TO_EVIDENCE_KIND
    ("policy_document", "policy.read"),  # SOURCE_TYPE_TO_EVIDENCE_KIND
    ("telemetry", "metric.read"),  # URI_SCHEME_TO_EVIDENCE_KIND: the exact independent-review repro
    ("policy-decision", "policy.read"),  # URI_SCHEME_TO_EVIDENCE_KIND
    ("genuinely-unknown-source-type-xyz", None),
]


@pytest.mark.parametrize("source_type,required_capability", _ALIAS_KIND_CASES)
def test_kw03_list_and_detail_agree_on_source_type_alias_kind(
    source_type: str, required_capability: Optional[str]
) -> None:
    """Regression for the [P2] research/service.py:1893-1904 second
    kind-resolution policy: ``_evidence_detail_payload`` used to pre-resolve
    kind through its own local ``SOURCE_TYPE_TO_EVIDENCE_KIND`` lookup and
    hand the canonical redactor a synthetic ref carrying only ``ref_id`` +
    ``evidence_type``, so ``source_document`` (and therefore any
    ``URI_SCHEME_TO_EVIDENCE_KIND`` alias such as ``telemetry``) never
    reached the canonical resolver and detail silently disagreed with list.
    For every source_type alias the canonical resolver knows -- plus a
    genuinely unknown one -- list and detail must now resolve identically."""
    ref_id = f"evref-alias-{source_type.replace('-', '_')}"
    port = _AliasKindPort(ref_id, source_type)

    with _stub_auth_env():
        admin_client = _alias_kind_client(port)
        low_client = _alias_kind_client(port)

        admin_list = admin_client.get(
            "/api/v1/knowledge/evidence", headers={"Authorization": FULL_CAPABILITY_AUTH}
        )
        admin_detail = admin_client.get(
            f"/api/v1/knowledge/evidence/{ref_id}", headers={"Authorization": FULL_CAPABILITY_AUTH}
        )
        low_list = low_client.get(
            "/api/v1/knowledge/evidence", headers={"Authorization": LOW_CAPABILITY_AUTH}
        )
        low_detail = low_client.get(
            f"/api/v1/knowledge/evidence/{ref_id}", headers={"Authorization": LOW_CAPABILITY_AUTH}
        )

    for resp in (admin_list, admin_detail, low_list, low_detail):
        assert resp.status_code == 200, resp.text

    admin_list_ref = next(
        item for item in admin_list.json()["evidence_refs"] if item.get("ref_id") == ref_id
    )
    admin_detail_body = admin_detail.json()
    low_list_ref = next(
        item for item in low_list.json()["evidence_refs"] if item.get("ref_id") == ref_id
    )
    low_detail_body = low_detail.json()

    if required_capability is None:
        for label, item in (
            ("admin list", admin_list_ref),
            ("admin detail", admin_detail_body),
            ("low list", low_list_ref),
            ("low detail", low_detail_body),
        ):
            assert item.get("redacted") is True, f"{label}: {item}"
            assert item.get("required_capability") == "unknown", f"{label}: {item}"
            assert item.get("reason") == "unresolved_evidence_kind", f"{label}: {item}"
        return

    # admin (full capability) sees the ref via both list and detail.
    assert admin_list_ref.get("redacted") is not True, admin_list_ref
    assert admin_detail_body.get("redacted") is not True, admin_detail_body

    # a low-capability identity lacking the mapped capability is withheld
    # via both list and detail, naming that exact capability.
    assert low_list_ref.get("redacted") is True, low_list_ref
    assert low_list_ref.get("required_capability") == required_capability, low_list_ref
    assert low_detail_body.get("redacted") is True, low_detail_body
    assert low_detail_body.get("required_capability") == required_capability, low_detail_body


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
        # Reuse the seeded app but build a client with
        # ``raise_server_exceptions=False`` so a genuine fail-open regression
        # (an unhandled ``RuntimeError`` reaching the ASGI boundary) surfaces
        # as an HTTP response this test can assert on behaviourally, rather
        # than as a rethrown exception pytest reports as an error instead of
        # a failed assertion.
        outage_client = TestClient(client.app, raise_server_exceptions=False)
        response = outage_client.get(
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


# ===========================================================================
# 8. Wrapper-caller surface: governance/service.py committee_projection via
#    the real governance router. Unlike callers 1-8 above (which all call
#    ``redact_evidence_refs`` directly), this surface goes through the
#    ``safe_redact_evidence_refs``/``safe_redact_scalar_ref`` wrappers in
#    models.py, so it exercises the wrapper's own capability-resolution
#    fail-closed path (``reason=redaction_policy_unavailable``) rather than
#    the base function's ``capabilities=None`` path exercised above.
# ===========================================================================

class _CommitteeReadStore:
    """Minimal read-store double exposing only what committee_projection needs."""

    def dataset_source(self, dataset: str) -> str:
        return "service_store"

    def get_committee(self, committee_id: str) -> Optional[dict]:
        if committee_id != "committee-fc-001":
            return None
        return {
            "committee_id": "committee-fc-001",
            "quorum_state": "quorum_met",
            "consensus_state": "sponsor_required",
            "linked_evidence": [
                {"ref_id": "policy-doc-fc-2", "evidence_type": "policy"},
            ],
        }


def _committee_client(*, capabilities_for_identity: Any = None) -> TestClient:
    store = _CommitteeReadStore()
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


def test_committee_projection_wrapper_redacts_for_low_capability_and_passes_for_full() -> None:
    with _stub_auth_env():
        low = _committee_client().get(
            "/api/v1/committees/committee-fc-001",
            headers={"Authorization": LOW_CAPABILITY_AUTH},
        )
        full = _committee_client().get(
            "/api/v1/committees/committee-fc-001",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )

    assert low.status_code == 200, low.text
    assert full.status_code == 200, full.text
    low_refs = low.json()["linked_evidence"]
    full_refs = full.json()["linked_evidence"]
    assert low_refs, "seeded committee linked_evidence must not be empty"
    # operator role lacks policy.read.
    assert all(ref.get("redacted") is True for ref in low_refs)
    assert all(ref.get("required_capability") == "policy.read" for ref in low_refs)
    assert not any(ref.get("redacted") is True for ref in full_refs), (
        "admin must see every evidence ref it could see before this change"
    )


def test_committee_projection_wrapper_fails_closed_when_capabilities_unresolvable() -> None:
    def _boom(identity: Any) -> list:
        raise RuntimeError("capability lookup unavailable")

    with _stub_auth_env():
        response = _committee_client(capabilities_for_identity=_boom).get(
            "/api/v1/committees/committee-fc-001",
            headers={"Authorization": FULL_CAPABILITY_AUTH},
        )
    assert response.status_code == 200, response.text
    refs = response.json()["linked_evidence"]
    assert refs, "seeded committee linked_evidence must not be empty"
    assert all(ref.get("redacted") is True for ref in refs), (
        "a raised capability lookup must fail closed even for an admin-shaped token"
    )
    assert all(ref.get("reason") == "redaction_policy_unavailable" for ref in refs), (
        "the wrapper's own capability-resolution-failure path must report "
        "redaction_policy_unavailable, distinct from insufficient_capability"
    )
