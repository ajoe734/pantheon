"""BFF-STRATEGY-LIST-RISK-TRUTH-20261106: list risk comes from evaluation_summary.risk_level.

Real hosted row shape (reg-strategy-spec-persona-298e138c136c9840b0e4): top level risk empty,
metadata.risk empty, governance has only ``risk_profile`` (no ``risk_level``),
execution_profile.risk_level empty, evaluation_summary.risk_level == "low".
"""
from __future__ import annotations

from typing import Any, List, Tuple

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.tests.test_strategy_list_default_view import (
    TENANT_A_HEADERS,
    _build_registry_backed_app,
    _make_registry_entry,
    _permissive_auth,  # noqa: F401  (autouse fixture)
)
from services.registry.models import ArtifactState

SID = "strategy-persona-298e138c136c9840b0e4"


def _persona_entry(registry_id: str, sid: str, state: ArtifactState, risk):
    entry = _make_registry_entry(
        registry_id=registry_id,
        strategy_id=sid,
        version="1.0.0",
        artifact_state=state,
        owner_tenant="tenant-dev",
        title=f"Persona {sid}",
        updated_at="2026-11-05T10:00:00Z",
    )
    spec = entry.metadata["strategy_spec"]
    spec["governance"] = {"risk_profile": "low: paper-only Persona admission, zero live capital"}
    spec["execution_profile"]["risk_level"] = ""
    entry.metadata["risk"] = ""
    spec["risk"] = ""
    entry.evaluation_summary = {"risk_level": risk} if risk else {}
    return entry


def test_list_and_detail_report_low_from_evaluation_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        _persona_entry("reg-strategy-spec-persona-298e138c136c9840b0e4", SID, ArtifactState.APPROVED, "low"),
        _persona_entry("reg-strategy-spec-persona-nolevel", "strategy-persona-nolevel", ArtifactState.APPROVED, None),
    ]
    calls: List[Tuple[str, int]] = []
    app, _ = _build_registry_backed_app(monkeypatch, entries, calls)
    client = TestClient(app)

    listing = client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=TENANT_A_HEADERS)
    assert listing.status_code == 200, listing.text
    rows = {r["id"]: r for r in listing.json()["items"]}
    assert rows[SID]["risk"] == "low"
    # Unknown risk keeps the existing medium fallback (separate decision).
    assert rows["strategy-persona-nolevel"]["risk"] == "medium"


def _pg_assembled_rows(entries, visible):
    """Rows exactly as the Postgres owner assembles them from its SQL leaf columns."""
    import json

    from services.registry.pg_store import (
        _STRATEGY_SUMMARY_CONTAINER_PATHS,
        _strategy_summary_leaf_paths,
        strategy_spec_summary_rows_from_columns,
    )

    def _walk(payload: Any, p: tuple):
        cur = payload
        for k in p:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur

    sql_rows = []
    for e in entries:
        if e.owner_tenant not in visible:
            continue
        d = e.to_dict()
        leaves = []
        for p in _strategy_summary_leaf_paths():
            v = _walk(d, p)
            leaves.append(None if v is None else json.dumps(v))
        shapes = []
        for p in _STRATEGY_SUMMARY_CONTAINER_PATHS:
            v = _walk(d, p)
            shapes.append(None if v is None else ("object" if isinstance(v, dict) else "scalar"))
        sql_rows.append(tuple(leaves) + tuple(shapes))
    return strategy_spec_summary_rows_from_columns(sql_rows)


def test_postgres_summary_projection_carries_risk_to_bff_list(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        _persona_entry("reg-strategy-spec-persona-298e138c136c9840b0e4", SID, ArtifactState.APPROVED, "low"),
        _persona_entry("reg-strategy-spec-persona-nolevel", "strategy-persona-nolevel", ArtifactState.APPROVED, None),
    ]
    calls: List[Tuple[str, int]] = []
    app, reg_service = _build_registry_backed_app(monkeypatch, entries, calls)
    pg_rows = _pg_assembled_rows(entries, frozenset({"tenant-dev", "__builtin__"}))
    assert pg_rows[0]["evaluation_summary"] == {"risk_level": "low"}
    assert "evaluation_summary" not in pg_rows[1]
    monkeypatch.setattr(
        reg_service.store, "list_strategy_spec_summary_rows", lambda **_kw: pg_rows
    )
    client = TestClient(app)
    listing = client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=TENANT_A_HEADERS)
    assert listing.status_code == 200, listing.text
    rows = {r["id"]: r for r in listing.json()["items"]}
    assert rows[SID]["risk"] == "low"
    assert rows["strategy-persona-nolevel"]["risk"] == "medium"
