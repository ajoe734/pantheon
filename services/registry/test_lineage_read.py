"""REGISTRY-LINEAGE-OWNER-READ-20261104: registry-owned bounded lineage read.

Fixtures use the real row shapes observed on the hosted registry (persona
provisioning family fd1281847a3b1c6a9735, a distilled spec, JSON-null lineage
arrays), not synthetic edge lists.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256

from .models import RegistryEntry
from .service import app
from .storage import (
    LINEAGE_LIST_FIELDS, LINEAGE_META_FIELDS, LINEAGE_ROW_KEYS, LINEAGE_SCALAR_FIELDS,
    build_lineage_graph, get_store, lineage_row, reset_store,
)

_SECRET = "lineage-read-test-secret"
FD = "fd1281847a3b1c6a9735"


def _entry(registry_id, artifact_type, strategy_id, *, lineage=None, metadata=None,
           state="draft", tenant="tenant-a", created_at="2026-11-01T00:00:00Z", version="1.0.0"):
    return RegistryEntry.from_dict({
        "registry_id": registry_id, "artifact_type": artifact_type, "strategy_id": strategy_id,
        "version": version, "artifact_state": state, "lineage": lineage or {},
        "storage_ref": {"backend": "inline", "path": "x"}, "checksum": "sha256:x",
        "metadata": metadata, "owner_tenant": tenant, "created_at": created_at, "updated_at": created_at,
    })


def persona_family_entries(tenant="tenant-a"):
    meta = {"persona_id": f"persona-{FD}", "capital_pool_id": f"pool-{FD}"}
    return [
        _entry(f"reg-strategy-spec-persona-baseline-{FD}", "strategy_spec", f"strategy-persona-{FD}",
               lineage={"source_run_ids": [f"persona-provisioning-forward-{FD}"]}, tenant=tenant,
               metadata=meta, created_at="2026-11-01T00:00:00Z"),
        _entry(f"reg-strategy-spec-persona-{FD}", "strategy_spec", f"strategy-persona-{FD}",
               lineage={"source_run_ids": [f"persona-provisioning-forward-{FD}"],
                        "parent_registry_ids": [f"reg-strategy-spec-persona-baseline-{FD}"]},
               tenant=tenant, metadata=meta, version="1.0.1", created_at="2026-11-01T01:00:00Z"),
        _entry(f"artifact-persona-paper-baseline-{FD}", "execution_bundle", f"strategy-persona-{FD}",
               lineage={"source_run_ids": [f"persona-provisioning-forward-artifact-{FD}"],
                        "source_strategy_spec_id": f"reg-strategy-spec-persona-baseline-{FD}"},
               tenant=tenant, metadata=meta, created_at="2026-11-01T02:00:00Z"),
        _entry(f"artifact-persona-paper-{FD}", "execution_bundle", f"strategy-persona-{FD}",
               lineage={"source_run_ids": [f"persona-provisioning-forward-artifact-{FD}"],
                        "parent_registry_ids": [f"artifact-persona-paper-baseline-{FD}"],
                        "source_strategy_spec_id": f"reg-strategy-spec-persona-{FD}"},
               state="approved", tenant=tenant, metadata=meta, created_at="2026-11-01T03:00:00Z"),
    ]


def large_corpus(n=5251):
    """5251 rows with the hosted shape: 4999 strategy_spec, 251 execution_bundle, 1 other; mostly draft."""
    rows = [lineage_row(e) for e in persona_family_entries()]
    for i in range(n - len(rows)):
        if i < 249:  # remaining bundles (251 total with the family's 2)
            rows.append(lineage_row(_entry(
                f"artifact-{i:05d}", "execution_bundle", f"strat-{i:05d}",
                lineage={"source_run_ids": [f"run-{i:05d}"], "parent_registry_ids": [f"reg-spec-{i:05d}"],
                         "source_strategy_spec_id": f"reg-spec-{i:05d}"},
                created_at=f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:00:00Z")))
        elif i == n - len(rows) - 1:  # the single non-spec/non-bundle entry (4999 + 251 + 1 = 5251)
            rows.append(lineage_row(_entry(f"feature-{i:05d}", "feature_set", f"feat-{i:05d}", lineage={"source_run_ids": [f"run-f{i}"]})))
        else:
            rows.append(lineage_row(_entry(
                f"reg-spec-{i:05d}", "strategy_spec", f"strat-{i:05d}",
                lineage={"source_run_ids": [f"seed-{i:05d}"], "source_dataset_refs": [f"src-dev-product-{i:05d}"],
                         "parent_registry_ids": None if i % 3 else [f"reg-spec-{i - 1:05d}"]},
                metadata={"persona_id": f"persona-{i % 40}"}, created_at=f"2026-09-{1 + i % 28:02d}T00:00:00Z")))
    return rows


def _edge_set(graph):
    return {(e["from"], e["to"], e["label"]) for e in graph["edges"]}


def test_persona_family_chain_from_real_shape():
    g = build_lineage_graph([lineage_row(e) for e in persona_family_entries()],
                            root_id=f"strategy-persona-{FD}", depth=3, limit=200)
    edges = _edge_set(g)
    base_spec, spec = f"reg-strategy-spec-persona-baseline-{FD}", f"reg-strategy-spec-persona-{FD}"
    base_art, art = f"artifact-persona-paper-baseline-{FD}", f"artifact-persona-paper-{FD}"
    assert (base_spec, spec, "parent") in edges
    assert (spec, art, "source_strategy_spec") in edges
    assert (base_art, art, "parent") in edges
    assert (base_spec, base_art, "source_strategy_spec") in edges
    assert (art, f"persona-{FD}", "persona") in edges
    assert (art, f"pool-{FD}", "capital_pool") in edges
    types = {n["id"]: n.get("type") for n in g["nodes"]}
    assert types[base_spec] == types[spec] == "Strategy"
    assert types[base_art] == types[art] == "Artifact"
    assert types[f"persona-{FD}"] == "Persona" and types[f"pool-{FD}"] == "CapitalPool"
    assert types[f"persona-provisioning-forward-{FD}"] == "Experiment"
    node = next(n for n in g["nodes"] if n["id"] == art)
    assert node["state"] == "approved" and "risk" not in node  # owner has no risk: none invented
    assert g["has_more"] is False


def test_null_and_absent_lineage_fields_produce_no_edges():
    rows = [lineage_row(_entry("a", "strategy_spec", "s", lineage={
        "parent_registry_ids": None, "source_run_ids": None, "source_dataset_refs": None}))]
    rows.append(lineage_row(_entry("b", "execution_bundle", "s2", lineage={})))
    g = build_lineage_graph(rows)
    assert g["edges"] == [] and g["nodes"] == [] and g["has_more"] is False


def test_root_kinds_and_depth():
    rows = [lineage_row(e) for e in persona_family_entries()]
    art = f"artifact-persona-paper-{FD}"
    d1 = build_lineage_graph(rows, root_id=art, depth=1)
    assert _edge_set(d1) == {
        (f"reg-strategy-spec-persona-{FD}", art, "source_strategy_spec"),
        (f"artifact-persona-paper-baseline-{FD}", art, "parent"),
        (f"persona-provisioning-forward-artifact-{FD}", art, "source_run"),
        (art, f"persona-{FD}", "persona"), (art, f"pool-{FD}", "capital_pool"),
    }
    d3 = build_lineage_graph(rows, root_id=art, depth=3)
    assert len(d3["edges"]) > len(d1["edges"])
    by_persona = build_lineage_graph(rows, root_id=f"persona-{FD}", depth=1)
    assert len(by_persona["edges"]) == 4  # every family entry binds the persona
    assert build_lineage_graph(rows, root_id="nope")["nodes"] == []


def test_default_view_is_bounded_and_never_returns_draft_specs_wholesale():
    rows = large_corpus()
    assert len(rows) == 5251
    g = build_lineage_graph(rows, limit=50)
    assert len(g["edges"]) <= 50 and g["has_more"] is True
    ids = {n["id"] for n in g["nodes"]}
    assert len(ids) < 500
    bundles = [r for r in rows if r["artifact_type"] == "execution_bundle"]
    newest = max(bundles, key=lambda r: (r["created_at"], r["registry_id"]))["registry_id"]
    assert newest in ids


def test_row_projection_matches_column_assembly():
    from .pg_store import lineage_rows_from_columns
    names = list(LINEAGE_ROW_KEYS) + list(LINEAGE_LIST_FIELDS + LINEAGE_SCALAR_FIELDS) + list(LINEAGE_META_FIELDS)
    for e in persona_family_entries():
        row = lineage_row(e)
        cols = tuple(json.dumps(row[n]) if row[n] is not None else None for n in names)
        assert lineage_rows_from_columns([cols]) == [row]


@pytest.fixture
def strict_client(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", _SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    reset_store()
    with TestClient(app) as client:
        yield client
    reset_store()


def _auth(tenant, roles=("operator",)):
    tok = encode_jwt_hs256({"sub": "u", "tenant": tenant, "roles": list(roles), "exp": time.time() + 3600}, secret=_SECRET)
    return {"Authorization": f"Bearer {tok}"}


def test_http_lineage_is_tenant_scoped(strict_client):
    store = get_store()
    for e in persona_family_entries("tenant-a"):
        store.put(e)
    for e in persona_family_entries("tenant-b"):
        e.registry_id = "b-" + e.registry_id
        store.put(e)
    art = f"artifact-persona-paper-{FD}"
    r = strict_client.get(f"/api/registry/lineage?root_id={art}&depth=1", headers=_auth("tenant-a"))
    assert r.status_code == 200
    body = r.json()
    assert {n["id"] for n in body["nodes"]} >= {art, f"persona-{FD}"}
    assert not any(n["id"].startswith("b-") for n in body["nodes"])
    other = strict_client.get(f"/api/registry/lineage?root_id=b-{art}", headers=_auth("tenant-a")).json()
    assert other["nodes"] == [] and other["edges"] == []
    admin = strict_client.get("/api/registry/lineage?limit=1000", headers=_auth("ops", roles=("admin",))).json()
    assert any(n["id"].startswith("b-") for n in admin["nodes"])
    assert strict_client.get("/api/registry/lineage").status_code in (401, 403)
    assert strict_client.get("/api/registry/lineage?limit=0", headers=_auth("tenant-a")).status_code == 422
