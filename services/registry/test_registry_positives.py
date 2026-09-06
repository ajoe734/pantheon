"""Registry "positives" must be real capability proofs, not "the route
exists and returns 200" — reviewer finding 3.

Three concrete previously-broken behaviors, reproduced exactly as probes and
asserted against the corrected status codes:

(a) POST /api/registry/strategy-specs with only a name/strategy_id (no full
    valid StrategySpec, lineage, or checksum) must be rejected (400), not
    accepted as an implicit "name-only draft".
(b) A full strategy_spec submitted alongside an incorrect/mismatched
    checksum must be rejected (400) — previously returned 200 because the
    caller-supplied checksum was never verified against the computed digest
    of the supplied strategy_spec.
(c) An arbitrary/out-of-sequence version (e.g. "9.9.9") with no valid
    parent/base linkage must be rejected (400) — previously any semver
    string was accepted regardless of what versions already existed for the
    strategy_id.

Also covers the fix 2 immutable-metadata-key regression: a metadata PATCH
attempting to replace metadata.strategy_spec on an existing entry must be
rejected, not silently applied.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .service import app
from .storage import reset_store

_AUTH = {"Authorization": "Bearer test-operator:operator"}


@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield
    reset_store()


@pytest.fixture
def client():
    return TestClient(app, headers=_AUTH)


def _lineage():
    return {"source_run_ids": ["run-1"]}


def _valid_spec(strategy_id: str, **variant_metadata) -> dict:
    """A minimal but schema-valid StrategySpec (services/control-plane/specs/
    strategy_spec.schema.json) — reviewer finding 2 requires the dedicated
    registration route to actually enforce this schema, so these tests must
    submit schema-complete payloads to exercise the *other* behaviors
    (checksum, version sequencing, immutable-metadata guard) they target.
    ``variant_metadata`` lands under the schema's open ``metadata`` object so
    tests can still distinguish otherwise-identical payload variants without
    violating the schema's ``additionalProperties: false`` at the root.
    """
    spec = {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "title": "Positives probe strategy",
        "hypothesis": "Deterministic probe hypothesis for registry positive-capability tests.",
        "objective": "Prove real registry write-owner capability, not just route existence.",
        "market_scope": {"symbols": ["TEST"], "frequency": "1d"},
        "data_dependencies": [{"ref": "test-fixture", "kind": "note"}],
        "execution_profile": {"signal_schema_version": "1.0", "quantity_type": "SHARES"},
        "evaluation_plan": {"metrics": ["sharpe"]},
        "governance": {"approval_required": True},
        "provenance": {"source_kind": "manual", "created_at": "2026-01-01T00:00:00Z"},
    }
    if variant_metadata:
        spec["metadata"] = dict(variant_metadata)
    return spec


def test_register_strategy_spec_rejects_name_only_draft_with_no_spec_lineage_or_checksum(client):
    """A bare {strategy_id, version} POST — no strategy_spec, no lineage, no
    checksum, no storage_ref — must be rejected explicitly, not accepted as
    an implicit capability-complete registration."""
    resp = client.post(
        "/api/registry/strategy-specs",
        json={"strategy_id": "positives-name-only", "version": "1.0.0"},
    )
    assert resp.status_code == 400, resp.text


def test_register_strategy_spec_rejects_mismatched_checksum(client):
    """Supplying a full strategy_spec alongside an explicit checksum that
    does not match the computed digest of that payload must be rejected —
    this previously returned 200 because the caller-supplied checksum was
    accepted verbatim without verification."""
    strategy_spec = _valid_spec("positives-checksum")
    resp = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": "positives-checksum",
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": strategy_spec,
            "checksum": "sha256:0000000000000000000000000000000000000000000000000000000000aa",
        },
    )
    assert resp.status_code == 400, resp.text
    assert "checksum" in resp.json()["detail"].lower()


def test_register_strategy_spec_accepts_correct_checksum(client):
    """The positive counterpart: a correct checksum (or an omitted one,
    computed server-side) for the same payload must still succeed."""
    strategy_spec = _valid_spec("positives-checksum-ok")
    resp = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": "positives-checksum-ok",
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": strategy_spec,
        },
    )
    assert resp.status_code == 200, resp.text


def test_register_strategy_spec_rejects_out_of_sequence_version_without_parent_link(client):
    """After version 1.0.0 exists for a strategy_id, registering an
    unrelated jump like "9.9.9" with no parent_registry_ids must be
    rejected — previously any semver string was accepted regardless of
    existing versions for the strategy_id."""
    strategy_id = "positives-version-seq"
    first = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id),
        },
    )
    assert first.status_code == 200, first.text

    jump = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "9.9.9",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id, v=2),
        },
    )
    assert jump.status_code == 400, jump.text
    assert "version" in jump.json()["detail"].lower()


def test_register_strategy_spec_accepts_valid_next_version(client):
    strategy_id = "positives-version-next"
    first = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id),
        },
    )
    assert first.status_code == 200, first.text
    parent_id = first.json()["entry"]["registry_id"]
    parent_checksum = first.json()["entry"]["checksum"]

    # Noninitial revision without parent/base identity is rejected (400)
    unbound = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.1",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id, v=2),
        },
    )
    assert unbound.status_code == 400, unbound.text
    assert "caller parent/base identity" in unbound.json()["detail"]

    # Valid next revision with parent linkage succeeds (200)
    next_patch = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.1",
            "lineage": {"source_run_ids": ["run-1"], "parent_registry_ids": [parent_id]},
            "base_checksum": parent_checksum,
            "strategy_spec": _valid_spec(strategy_id, v=2),
        },
    )
    assert next_patch.status_code == 200, next_patch.text


def test_register_strategy_spec_accepts_version_with_valid_parent_link(client):
    strategy_id = "positives-version-parent"
    first = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id),
        },
    )
    assert first.status_code == 200, first.text
    parent_id = first.json()["entry"]["registry_id"]

    jump_with_parent = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "9.9.9",
            "lineage": {"source_run_ids": ["run-1"], "parent_registry_ids": [parent_id]},
            "strategy_spec": _valid_spec(strategy_id, v=3),
        },
    )
    assert jump_with_parent.status_code == 200, jump_with_parent.text


def test_metadata_patch_cannot_overwrite_immutable_strategy_spec_key(client):
    """A metadata PATCH attempting to replace metadata.strategy_spec on an
    existing entry must be rejected, not silently applied — reviewer
    finding 2."""
    strategy_id = "positives-immutable"
    created = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id),
        },
    )
    assert created.status_code == 200, created.text
    registry_id = created.json()["entry"]["registry_id"]
    current_metadata = created.json()["entry"]["metadata"]

    tampered_metadata = dict(current_metadata)
    tampered_metadata["strategy_spec"] = {"strategy_id": strategy_id, "spec_version": "9.9-tampered"}

    resp = client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": current_metadata, "metadata": tampered_metadata},
    )
    assert resp.status_code == 409, resp.text

    unchanged = client.get(f"/api/registry/entries/{registry_id}")
    assert unchanged.json()["entry"]["metadata"]["strategy_spec"] == _valid_spec(strategy_id)


def test_metadata_patch_cannot_drop_immutable_strategy_spec_key(client):
    """Wiping metadata entirely (e.g. metadata=None or metadata={}) must
    also be rejected once an immutable key is set — dropping the key is
    just as destructive as replacing its value."""
    strategy_id = "positives-immutable-drop"
    created = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id),
        },
    )
    assert created.status_code == 200, created.text
    registry_id = created.json()["entry"]["registry_id"]
    current_metadata = created.json()["entry"]["metadata"]

    resp = client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": current_metadata, "metadata": {}},
    )
    assert resp.status_code == 409, resp.text


def test_metadata_patch_still_allows_non_reserved_metadata_updates(client):
    """The immutability guard must be scoped to the reserved keys only —
    ordinary operator metadata notes remain freely editable."""
    strategy_id = "positives-immutable-allowed"
    created = client.post(
        "/api/registry/strategy-specs",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": _lineage(),
            "strategy_spec": _valid_spec(strategy_id),
        },
    )
    assert created.status_code == 200, created.text
    registry_id = created.json()["entry"]["registry_id"]
    current_metadata = created.json()["entry"]["metadata"]

    updated_metadata = dict(current_metadata)
    updated_metadata["operator_note"] = "reviewed"

    resp = client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": current_metadata, "metadata": updated_metadata},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["entry"]["metadata"]["operator_note"] == "reviewed"
    assert resp.json()["entry"]["metadata"]["strategy_spec"] == _valid_spec(strategy_id)
