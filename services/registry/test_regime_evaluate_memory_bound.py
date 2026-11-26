"""Bounded SQL projection, semantic parity, and memory-bound tests for
``POST /api/registry/regime/evaluate`` (REGISTRY-REGIME-EVALUATE-BOUNDED-SQL-READ-20261126).
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import tracemalloc
from typing import Any, Mapping, Sequence
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from . import main as registry_main
from . import service as svc
from .models import (
    BUILTIN_TENANT,
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    StorageBackend,
    StorageRef,
)
from .pg_store import (
    _REGIME_STRATEGY_CONTAINER_PATHS,
    PostgresRegistryStore,
    _regime_model_artifact_leaf_paths,
    _regime_strategy_spec_leaf_paths,
    regime_model_artifact_rows_from_columns,
    regime_strategy_spec_rows_from_columns,
)
from .regime import routes as regime_routes
from .regime.profiles import profile_for
from .regime.store import get_regime_store
from .split_api import RegistryService
from .storage import (
    RegistryStore,
    regime_evaluate_model_row,
    regime_evaluate_strategy_row,
    reset_store,
    select_latest_regime_strategy_rows,
)
from .test_strategy_summary_page_bounded import (
    _HEAVY_SUMMARY_SPEC_KEYS,
    _realistic_strategy_spec_payload,
    _SpyMapping,
)
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "regime-bounded-sql-read-test-secret"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "pantheon-test-issuer")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "pantheon-test-audience")
    reset_store()
    yield
    reset_store()


def _auth_headers(tenant: str = "tenant-a", roles: Sequence[str] = ("operator",), sub: str = "op-1") -> dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": sub,
            "tenant": tenant,
            "roles": list(roles),
            "iss": "pantheon-test-issuer",
            "aud": "pantheon-test-audience",
            "exp": time.time() + 3600,
        },
        secret=SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _make_spec_entry(
    registry_id: str,
    strategy_id: str,
    version: str = "1.0.0",
    *,
    state: ArtifactState = ArtifactState.DRAFT,
    tenant: str | None = "tenant-a",
    metadata: dict[str, Any] | None = None,
    bulk: bool = False,
) -> RegistryEntry:
    if metadata is None:
        spec = (
            _realistic_strategy_spec_payload(strategy_id, version=version, lifecycle_state=state.value)
            if bulk
            else {"strategy_id": strategy_id, "spec_version": version, "title": f"Spec {strategy_id}"}
        )
        meta: dict[str, Any] | None = {"strategy_spec": spec, "tenant_id": tenant}
    else:
        meta = metadata
    return RegistryEntry(
        registry_id=registry_id,
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id=strategy_id,
        version=version,
        artifact_state=state,
        lineage=Lineage(source_run_ids=[f"run-{registry_id}"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum=f"sha256:{registry_id}",
        evaluation_summary={"sharpe": 1.5, "risk_level": "medium"},
        approval_evidence={"decision": f"gov-{registry_id}"} if state == ArtifactState.APPROVED else None,
        metadata=meta,
        created_at="2026-11-01T00:00:00Z",
        updated_at="2026-11-01T00:00:00Z",
        owner_tenant=tenant,
    )


def _legacy_select_strategies_and_models(
    store: RegistryStore | PostgresRegistryStore,
    ctx: Any,
    header_tenant: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reference legacy implementation from routes.py prior to REGISTRY-REGIME-EVALUATE-BOUNDED-SQL-READ-20261126."""
    registry = RegistryService(store)
    spec_views = [registry._to_view(e) for e in store.list_by_artifact_type(ArtifactType.STRATEGY_SPEC)]
    latest: dict[str, RegistryEntry] = {}
    for view in spec_views:
        e = view.entry
        if not (
            svc._can_read(ctx, view)
            and (header_tenant is None or e.owner_tenant in (header_tenant, BUILTIN_TENANT))
        ):
            continue
        if e.artifact_state.value == "retired":
            continue
        if e.strategy_id not in latest or regime_routes._semver(e.version) >= regime_routes._semver(
            latest[e.strategy_id].version
        ):
            latest[e.strategy_id] = e
    strategies = [
        {
            "strategy_id": e.strategy_id,
            "registry_id": e.registry_id,
            "metadata": e.metadata or {},
            "owner_tenant": e.owner_tenant,
        }
        for e in latest.values()
    ]
    model_views = [registry._to_view(e) for e in store.list_by_artifact_type(ArtifactType.MODEL_ARTIFACT)]
    models = [
        {
            "registry_id": v.entry.registry_id,
            "artifact_state": v.entry.artifact_state.value,
            "metadata": v.entry.metadata or {},
            "created_at": v.entry.created_at,
        }
        for v in model_views
        if v.entry.artifact_state.value != "retired"
    ]
    return strategies, models


def _simulate_pg_strategy_columns(entries: Sequence[RegistryEntry], visible_tenants: frozenset | None) -> list[tuple]:
    """Simulate the exact column tuple returned by Postgres for each matching non-retired visible strategy_spec."""
    leaf_paths = _regime_strategy_spec_leaf_paths()
    out: list[tuple] = []
    for e in entries:
        if e.artifact_type != ArtifactType.STRATEGY_SPEC:
            continue
        if e.artifact_state == ArtifactState.RETIRED:
            continue
        if visible_tenants is not None and e.owner_tenant not in visible_tenants:
            continue
        p = e.to_dict()

        def _ext(path: tuple[str, ...]) -> str | None:
            cur: Any = p
            for k in path:
                if not isinstance(cur, dict) or k not in cur:
                    return None
                cur = cur[k]
            return json.dumps(cur)

        def _typ(path: tuple[str, ...]) -> str | None:
            cur: Any = p
            for k in path:
                if not isinstance(cur, dict) or k not in cur:
                    return None
                cur = cur[k]
            return "object" if isinstance(cur, dict) else ("array" if isinstance(cur, list) else "scalar")

        row_tuple = tuple(_ext(path) for path in leaf_paths) + tuple(
            _typ(path) for path in _REGIME_STRATEGY_CONTAINER_PATHS
        )
        out.append(row_tuple)
    return out


def _simulate_pg_model_columns(entries: Sequence[RegistryEntry]) -> list[tuple]:
    """Simulate the exact column tuple returned by Postgres for each non-retired model_artifact."""
    leaf_paths = _regime_model_artifact_leaf_paths()
    out: list[tuple] = []
    for e in entries:
        if e.artifact_type != ArtifactType.MODEL_ARTIFACT:
            continue
        if e.artifact_state == ArtifactState.RETIRED:
            continue
        p = e.to_dict()

        def _ext(path: tuple[str, ...]) -> str | None:
            cur: Any = p
            for k in path:
                if not isinstance(cur, dict) or k not in cur:
                    return None
                cur = cur[k]
            return json.dumps(cur)

        meta_val = p.get("metadata")
        meta_type = "object" if isinstance(meta_val, dict) else ("array" if isinstance(meta_val, list) else None)
        out.append(tuple(_ext(path) for path in leaf_paths) + (meta_type,))
    return out


def _seed_parity_population(store: RegistryStore) -> list[RegistryEntry]:
    """Seed a comprehensive population covering semver, retired, tenants, builtins, and all profile metadata paths."""
    entries: list[RegistryEntry] = [
        # 1. Multi-version semver numerical ordering (1.10.0 > 1.2.0 > 1.0.0) + retired higher version (2.0.0 ignored)
        _make_spec_entry(
            "reg-s1-v1", "strat-semver", "1.0.0",
            state=ArtifactState.APPROVED, tenant="tenant-a",
            metadata={"regime": {"primary_axis": "volatility", "horizon": "short"}},
        ),
        _make_spec_entry(
            "reg-s1-v2", "strat-semver", "1.2.0",
            state=ArtifactState.APPROVED, tenant="tenant-a",
            metadata={"regime": {"primary_axis": "volatility", "horizon": "medium"}},
        ),
        _make_spec_entry(
            "reg-s1-v10", "strat-semver", "1.10.0",
            state=ArtifactState.CANDIDATE, tenant="tenant-a",
            metadata={"strategy_spec": {"metadata": {"regime": {"primary_axis": "risk_appetite", "horizon": "short"}}}},
        ),
        _make_spec_entry(
            "reg-s1-v20-ret", "strat-semver", "2.0.0",
            state=ArtifactState.RETIRED, tenant="tenant-a",
            metadata={"regime": {"primary_axis": "volatility", "horizon": "short"}},
        ),
        # 2. Same-semver tie-breaking (`>=` picks later entry in store order)
        _make_spec_entry(
            "reg-tie-first", "strat-tie", "1.0.0",
            state=ArtifactState.DRAFT, tenant="tenant-a",
            metadata={"family": "mean_reversion"},
        ),
        _make_spec_entry(
            "reg-tie-second", "strat-tie", "1.0.0",
            state=ArtifactState.APPROVED, tenant="tenant-a",
            metadata={"family": "defensive", "holding_horizon_sessions": 10},
        ),
        # 3. Family with ONLY retired versions -> completely excluded
        _make_spec_entry(
            "reg-only-retired", "strat-retired-only", "1.0.0",
            state=ArtifactState.RETIRED, tenant="tenant-a",
            metadata={"family": "momentum"},
        ),
        # 4. Different profile metadata shapes: applicable_regimes, strategy_spec.regime, macro fallback, None metadata
        _make_spec_entry(
            "reg-alpha-tpl", "strat-alpha-tpl", "1.0.0",
            state=ArtifactState.APPROVED, tenant="tenant-a",
            metadata={"strategy_spec": {"applicable_regimes": ["risk_on", "risk_off"], "holding_horizon_sessions": 45}},
        ),
        _make_spec_entry(
            "reg-spec-regime", "strat-spec-regime", "1.0.0",
            state=ArtifactState.DRAFT, tenant="tenant-a",
            metadata={"strategy_spec": {"regime": {"primary_axis": "volatility", "horizon": "short"}}},
        ),
        _make_spec_entry(
            "reg-macro-fb", "strat-macro-fb", "1.0.0",
            state=ArtifactState.DRAFT, tenant="tenant-a",
            metadata={"regime": {"primary_axis": "macro"}, "strategy_family": "mean_reversion"},
        ),
        RegistryEntry(
            registry_id="reg-none-meta",
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id="strat-none-meta",
            version="1.0.0",
            artifact_state=ArtifactState.DRAFT,
            lineage=Lineage(source_run_ids=["run-none"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum="sha256:none",
            metadata=None,
            created_at="2026-11-01T00:00:00Z",
            updated_at="2026-11-01T00:00:00Z",
            owner_tenant="tenant-a",
        ),
        # 5. Builtin strategy spec (visible to all tenants)
        _make_spec_entry(
            "reg-builtin-1", "strat-builtin", "1.0.0",
            state=ArtifactState.APPROVED, tenant=BUILTIN_TENANT,
            metadata={"strategy_spec": {"family": "mean_reversion"}},
        ),
        # 6. Other tenant (tenant-b) strategy spec + cross-tenant same strategy_id with higher version
        _make_spec_entry(
            "reg-b-only", "strat-b-only", "1.0.0",
            state=ArtifactState.APPROVED, tenant="tenant-b",
            metadata={"regime": {"primary_axis": "volatility", "horizon": "short"}},
        ),
        _make_spec_entry(
            "reg-shared-b-v9", "strat-semver", "9.0.0",
            state=ArtifactState.APPROVED, tenant="tenant-b",
            metadata={"regime": {"primary_axis": "volatility", "horizon": "short"}},
        ),
        # 7. Untenanted legacy row (only visible to admin without X-Tenant-Id or untenanted caller)
        _make_spec_entry(
            "reg-untenanted", "strat-untenanted", "1.0.0",
            state=ArtifactState.DRAFT, tenant=None,
            metadata={"family": "momentum"},
        ),
        # 8. Unrelated execution_bundle and model_artifact rows
        RegistryEntry(
            registry_id="reg-bundle-1",
            artifact_type=ArtifactType.EXECUTION_BUNDLE,
            strategy_id="strat-semver",
            version="9.9.9",
            artifact_state=ArtifactState.APPROVED,
            lineage=Lineage(source_run_ids=["run-b"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="inline:b"),
            checksum="sha256:bundle",
            metadata={"blob": "B" * 4096},
            created_at="2026-11-01T00:00:00Z",
            updated_at="2026-11-01T00:00:00Z",
            owner_tenant="tenant-a",
        ),
        RegistryEntry(
            registry_id="reg-model-active",
            artifact_type=ArtifactType.MODEL_ARTIFACT,
            strategy_id="regime-tw-volatility-short",
            version="1.0.0",
            artifact_state=ArtifactState.APPROVED,
            lineage=Lineage(source_run_ids=["run-m1"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="inline:m1"),
            checksum="sha256:m1",
            metadata={
                "regime_classifier": True,
                "market": "TW",
                "scope": "paper",
                "axis": "volatility",
                "horizon": "short",
                "model_version": "markov_switching@0.14/vol-short",
                "model_family": "markov_switching",
                "model_artifact_id": "vol-short",
                "model": {"params": [1, 2, 3]},
                "extra_unused_training_blob": "M" * 4096,
            },
            created_at="2026-11-02T00:00:00Z",
            updated_at="2026-11-02T00:00:00Z",
            owner_tenant="tenant-a",
        ),
        RegistryEntry(
            registry_id="reg-model-retired",
            artifact_type=ArtifactType.MODEL_ARTIFACT,
            strategy_id="regime-tw-volatility-short",
            version="2.0.0",
            artifact_state=ArtifactState.RETIRED,
            lineage=Lineage(source_run_ids=["run-m2"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="inline:m2"),
            checksum="sha256:m2",
            metadata={"regime_classifier": True, "market": "TW", "scope": "paper"},
            created_at="2026-11-03T00:00:00Z",
            updated_at="2026-11-03T00:00:00Z",
            owner_tenant="tenant-a",
        ),
    ]
    for e in entries:
        store.put(e)
    return entries


def test_semantic_parity_across_semver_retired_tenants_builtin_and_profile_metadata():
    """AC1 & AC2: Projected strategy_spec and model_artifact reads have exact semantic
    parity with the legacy list_views_by_artifact_type implementation across RegistryStore
    and Postgres SQL column decoding."""
    store = RegistryStore()
    entries = _seed_parity_population(store)

    scopes = [
        (_auth_headers("tenant-a")["Authorization"], None),
        (_auth_headers("tenant-a")["Authorization"], "tenant-a"),
        (_auth_headers("tenant-b", sub="op-b")["Authorization"], None),
        (_auth_headers("tenant-b", sub="op-b")["Authorization"], "tenant-b"),
        (_auth_headers("tenant-a", roles=("admin",), sub="admin-1")["Authorization"], None),
        (_auth_headers("tenant-a", roles=("admin",), sub="admin-1")["Authorization"], "tenant-a"),
    ]

    for bad_header in ("tenant-b", BUILTIN_TENANT, "   "):
        with pytest.raises(Exception) as exc_info:
            svc._strategy_spec_read_scope(_auth_headers("tenant-a")["Authorization"], bad_header)
        assert getattr(exc_info.value, "status_code", None) == 403

    for auth_header, x_tenant_id in scopes:
        ctx, header_tenant = svc._strategy_spec_read_scope(auth_header, x_tenant_id)
        visible_tenants = svc._persona_binding_visible_tenants(ctx, header_tenant)

        legacy_strategies, legacy_models = _legacy_select_strategies_and_models(store, ctx, header_tenant)
        mem_strategies = RegistryService(store).list_regime_evaluation_strategies(visible_tenants=visible_tenants)
        pg_col_rows = regime_strategy_spec_rows_from_columns(
            _simulate_pg_strategy_columns(entries, visible_tenants)
        )
        pg_strategies = select_latest_regime_strategy_rows(pg_col_rows, visible_tenants=visible_tenants)

        assert [(s["strategy_id"], s["registry_id"], s["owner_tenant"]) for s in mem_strategies] == [
            (s["strategy_id"], s["registry_id"], s["owner_tenant"]) for s in legacy_strategies
        ]
        assert mem_strategies == pg_strategies

        for leg, mem, pg in zip(legacy_strategies, mem_strategies, pg_strategies):
            assert profile_for(mem["metadata"]) == profile_for(leg["metadata"])
            assert profile_for(pg["metadata"]) == profile_for(leg["metadata"])

        mem_models = RegistryService(store).list_regime_model_artifact_rows()
        pg_models = regime_model_artifact_rows_from_columns(_simulate_pg_model_columns(entries))
        assert mem_models == pg_models
        assert len(mem_models) == len(legacy_models) == 1
        assert mem_models[0]["registry_id"] == "reg-model-active"
        assert "extra_unused_training_blob" not in mem_models[0]["metadata"]

    # Specifically verify tenant-a selections:
    ctx_a, _ = svc._strategy_spec_read_scope(_auth_headers("tenant-a")["Authorization"], None)
    vis_a = svc._persona_binding_visible_tenants(ctx_a, None)
    by_sid_a = {
        s["strategy_id"]: s
        for s in RegistryService(store).list_regime_evaluation_strategies(visible_tenants=vis_a)
    }
    assert by_sid_a["strat-semver"]["registry_id"] == "reg-s1-v10"  # 1.10.0 beats 1.2.0; retired 2.0.0 & tenant-b 9.0.0 excluded
    assert by_sid_a["strat-tie"]["registry_id"] == "reg-tie-second"  # `>=` tie-break picks second 1.0.0
    assert "strat-retired-only" not in by_sid_a
    assert "strat-b-only" not in by_sid_a
    assert "strat-untenanted" not in by_sid_a
    assert "strat-builtin" in by_sid_a


def test_evaluate_route_never_calls_list_views_by_artifact_type_or_reads_heavy_spec_leaves(monkeypatch):
    """AC1: POST /api/registry/regime/evaluate never calls list_views_by_artifact_type,
    list_by_artifact_type, list_all_entries, or _to_view, and never touches heavy StrategySpec leaves."""
    for mod in ("numpy", "pandas", "scipy", "statsmodels"):
        pytest.importorskip(mod)
    from services.registry.regime.tests import test_evaluator as S

    store = svc.get_store()
    heavy_reads: set[str] = set()

    for entry in S.model_entries("approved"):
        meta = entry["metadata"]
        rid = entry["registry_id"]
        store.put(
            RegistryEntry(
                registry_id=rid,
                artifact_type=ArtifactType.MODEL_ARTIFACT,
                strategy_id=f"regime-tw-{meta['axis']}-{meta['horizon']}",
                version="1.0.0",
                artifact_state=ArtifactState.APPROVED,
                lineage=Lineage(source_run_ids=["run-m"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="inline:m"),
                checksum=f"sha256:{rid}",
                metadata=meta,
                created_at=entry["created_at"],
                updated_at=entry["created_at"],
                owner_tenant="tenant-a",
            )
        )

    for i, sid in enumerate(("strat-vol-short", "strat-default-trend")):
        rid = f"reg-{sid}"
        raw_spec = _realistic_strategy_spec_payload(sid, version="1.0.0", lifecycle_state="approved")
        if i == 0:
            raw_spec["metadata"]["regime"] = {"primary_axis": "volatility", "horizon": "short"}
        spy_spec = _SpyMapping(
            raw_spec,
            registry_id=rid,
            heavy_keys=_HEAVY_SUMMARY_SPEC_KEYS,
            heavy_reads_by_row=heavy_reads,
        )
        store.put(
            _make_spec_entry(
                rid, sid, "1.0.0",
                state=ArtifactState.APPROVED,
                tenant="tenant-a",
                metadata={"strategy_spec": spy_spec, "tenant_id": "tenant-a"},
            )
        )

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("POST /api/registry/regime/evaluate must use bounded projection, not full RegistryEntry/View reads")

    monkeypatch.setattr(svc, "list_views_by_artifact_type", _forbidden)
    monkeypatch.setattr(RegistryService, "list_all_entries", _forbidden)
    monkeypatch.setattr(RegistryService, "_to_view", _forbidden)
    monkeypatch.setattr(RegistryStore, "list_by_artifact_type", _forbidden)
    monkeypatch.setattr(RegistryStore, "list_all_entries", _forbidden)
    monkeypatch.setattr(regime_routes, "_observations_loader", lambda: (lambda: S.observations()))

    client = TestClient(registry_main.app)
    session = S.sessions()[-1]
    resp = client.post(
        "/api/registry/regime/evaluate",
        headers=_auth_headers("tenant-a"),
        json={"session": session},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sets"][0]["computed"] is True
    assert len(body["sets"][0]["new_regime_ids"]) == 2
    assert heavy_reads == set(), f"Heavy StrategySpec leaves were unexpectedly read: {heavy_reads}"

    regimes = {
        r["record"]["strategy_id"]: r["record"]
        for r in get_regime_store().list_regimes(served_only=False)
    }
    assert set(regimes) == {"strat-vol-short", "strat-default-trend"}
    assert regimes["strat-vol-short"]["feature_snapshot"]["profile"]["primary_axis"] == "volatility"
    assert regimes["strat-vol-short"]["feature_snapshot"]["profile"]["horizon"] == "short"
    assert regimes["strat-default-trend"]["feature_snapshot"]["profile"]["primary_axis"] == "trend"


def test_postgres_sql_generation_filters_retired_and_tenant_and_unwraps_legacy_strings():
    """AC1 & AC2: PostgresRegistryStore generates single-pass SQL that filters
    artifact_type, artifact_state != 'retired', and visible owner_tenant in SQL,
    unwraps legacy JSON-string payloads, and projects only regime leaves."""
    captured: list[tuple[str, Any]] = []

    class _Cursor:
        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql: str, params: Any = ()):
            captured.append((sql, params))
            return _Cursor()

    class _Entries:
        table = '"registry"."entries"'

        @contextlib.contextmanager
        def _use_conn(self, conn):
            yield _Conn()

    pg = PostgresRegistryStore.__new__(PostgresRegistryStore)
    pg._entries = _Entries()

    visible = frozenset({"tenant-a", BUILTIN_TENANT})
    assert pg.list_regime_strategy_spec_rows(visible_tenants=visible) == []
    assert len(captured) == 1
    sql, params = captured[0]

    assert "payload ->> 'artifact_type' = %s" in sql
    assert "COALESCE(payload ->> 'artifact_state', '') <> 'retired'" in sql
    assert "payload ->> 'owner_tenant' = ANY(%s::text[])" in sql
    assert "CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb" in sql
    assert "payload || '{}'::jsonb" in sql
    assert "(payload)::text" not in sql
    for heavy_field in ("storage_ref", "approval_evidence", "evaluation_summary", "lineage", "description", "signals"):
        assert heavy_field not in sql
    assert params == (
        "strategy_spec",
        [BUILTIN_TENANT, "tenant-a"],
        "strategy_spec",
        [BUILTIN_TENANT, "tenant-a"],
    )

    captured.clear()
    assert pg.list_regime_model_artifact_rows() == []
    assert len(captured) == 1
    model_sql, model_params = captured[0]
    assert "payload ->> 'artifact_type' = %s" in model_sql
    assert "COALESCE(payload ->> 'artifact_state', '') <> 'retired'" in model_sql
    assert "CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb" in model_sql
    assert "(payload)::text" not in model_sql
    assert model_params == ("model_artifact", "model_artifact")

    # Also verify the list_all_selected fallback path when _entries has no _use_conn
    fallback_calls: list[tuple[str, str | None, tuple | None]] = []

    class _FallbackEntries:
        def list_all_selected(self, columns_sql: str, *, visible_sql: str | None = None, keep_params: tuple | None = None):
            fallback_calls.append((columns_sql, visible_sql, keep_params))
            return []

    pg_fb = PostgresRegistryStore.__new__(PostgresRegistryStore)
    pg_fb._entries = _FallbackEntries()
    assert pg_fb.list_regime_strategy_spec_rows(visible_tenants=visible) == []
    assert pg_fb.list_regime_model_artifact_rows() == []
    assert len(fallback_calls) == 2
    assert "COALESCE(payload ->> 'artifact_state', '') <> 'retired'" in (fallback_calls[0][1] or "")
    assert "COALESCE(payload ->> 'artifact_state', '') <> 'retired'" in (fallback_calls[1][1] or "")


def _record_evidence(label: str, data: dict[str, Any]) -> None:
    out = os.getenv("REGISTRY_REGIME_EVALUATE_EVIDENCE_OUT", "").strip()
    if not out:
        return
    try:
        with open(out, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, ValueError):
        existing = {}
    existing[label] = data
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2, sort_keys=True)


def test_hosted_scale_9148_strategy_specs_memory_bound_benchmark():
    """AC3 & AC4: Memory benchmark across 9,148 hosted-shape strategy_spec rows
    demonstrates bounded Python allocation without full-payload RegistryEntryView
    materialization."""
    # Hosted proportions from REGISTRY-MEMORY-LIMIT-PEAK-SIZED-20261125:
    # 7,421 draft, 170 approved, 1 candidate, 1,556 retired = 9,148 strategy_spec rows
    spec_template = _realistic_strategy_spec_payload("strat-template", version="1.0.0", lifecycle_state="draft")
    template_entry = RegistryEntry(
        registry_id="reg-template",
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id="strat-template",
        version="1.0.0",
        artifact_state=ArtifactState.DRAFT,
        lineage=Lineage(source_run_ids=["run-template"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum="sha256:" + "a" * 64,
        evaluation_summary={"sharpe": 1.72, "risk_level": "medium"},
        metadata={"strategy_spec": spec_template, "tenant_id": "tenant-dev"},
        created_at="2026-11-01T00:00:00Z",
        updated_at="2026-11-01T00:00:00Z",
        owner_tenant="tenant-dev",
    )
    raw_template_json = json.dumps(template_entry.to_dict(), sort_keys=True)
    assert len(raw_template_json) >= 5500

    state_schedule = (
        [(ArtifactState.DRAFT.value, 7421)]
        + [(ArtifactState.APPROVED.value, 170)]
        + [(ArtifactState.CANDIDATE.value, 1)]
        + [(ArtifactState.RETIRED.value, 1556)]
    )

    # Build the 7,592 non-retired SQL projected 17-column tuples that Postgres returns
    # (1,556 retired rows are filtered out in Postgres SQL before leaving the DB).
    projected_sql_tuples: list[tuple] = []
    idx = 0
    for state_val, count in state_schedule:
        for _ in range(count):
            idx += 1
            if state_val == ArtifactState.RETIRED.value:
                continue
            sid = f'"strat-hosted-{idx:05d}"'
            rid = f'"reg-hosted-{idx:05d}"'
            fam = '"mean_reversion"' if idx % 5 == 0 else None
            projected_sql_tuples.append(
                (
                    rid, sid, '"1.0.0"', f'"{state_val}"', '"tenant-dev"',
                    None, None, fam, '"Cross-Sectional Momentum"', None,
                    None, None, None, None,
                    None,
                    "object", "object",
                )
            )
    assert idx == 9148
    assert len(projected_sql_tuples) == 7592

    visible = frozenset({"tenant-dev", BUILTIN_TENANT})

    # Measure bounded projected decode + latest semver selection across 9,148 rows (7,592 non-retired)
    tracemalloc.start()
    tracemalloc.reset_peak()
    base_mem = tracemalloc.get_traced_memory()[0]
    t0 = time.perf_counter()
    decoded_rows = regime_strategy_spec_rows_from_columns(projected_sql_tuples)
    selected_strategies = select_latest_regime_strategy_rows(decoded_rows, visible_tenants=visible)
    bounded_elapsed_ms = (time.perf_counter() - t0) * 1000.0
    bounded_peak_bytes = tracemalloc.get_traced_memory()[1] - base_mem

    # Contrast with legacy full-payload decode + RegistryEntryView materialization of 9,148 rows
    tracemalloc.reset_peak()
    base_legacy = tracemalloc.get_traced_memory()[0]
    t1 = time.perf_counter()
    legacy_views = []
    for i in range(9148):
        d = json.loads(raw_template_json)
        d["registry_id"] = f"reg-hosted-{i:05d}"
        d["strategy_id"] = f"strat-hosted-{i:05d}"
        if i >= 7592:
            d["artifact_state"] = "retired"
        legacy_views.append(RegistryService._to_view(RegistryEntry.from_dict(d)))
    legacy_latest: dict[str, RegistryEntry] = {}
    for v in legacy_views:
        e = v.entry
        if e.artifact_state.value == "retired":
            continue
        if e.strategy_id not in legacy_latest or regime_routes._semver(e.version) >= regime_routes._semver(
            legacy_latest[e.strategy_id].version
        ):
            legacy_latest[e.strategy_id] = e
    legacy_strategies = [
        {
            "strategy_id": e.strategy_id,
            "registry_id": e.registry_id,
            "metadata": e.metadata or {},
            "owner_tenant": e.owner_tenant,
        }
        for e in legacy_latest.values()
    ]
    legacy_elapsed_ms = (time.perf_counter() - t1) * 1000.0
    legacy_peak_bytes = tracemalloc.get_traced_memory()[1] - base_legacy
    tracemalloc.stop()

    assert len(selected_strategies) == len(legacy_strategies) == 7592
    # Bounded path must stay under 12 MiB traced Python allocation for all 9,148 specs
    # and use at least 85% less memory than full RegistryEntryView decoding.
    assert bounded_peak_bytes < 12 * 1024 * 1024, (
        f"Bounded peak {bounded_peak_bytes / (1024 * 1024):.2f} MiB exceeded 12 MiB cap"
    )
    assert bounded_peak_bytes < legacy_peak_bytes * 0.15, (
        f"Expected >= 85% memory reduction: bounded={bounded_peak_bytes / (1024 * 1024):.2f} MiB, "
        f"legacy={legacy_peak_bytes / (1024 * 1024):.2f} MiB"
    )
    assert bounded_elapsed_ms < legacy_elapsed_ms * 0.25
    _record_evidence(
        "python_column_decode_benchmark_9148_specs",
        {
            "total_strategy_spec_rows": 9148,
            "non_retired_strategy_spec_rows": 7592,
            "retired_strategy_spec_rows_filtered_in_sql": 1556,
            "bounded_traced_peak_bytes": bounded_peak_bytes,
            "bounded_traced_peak_mib": round(bounded_peak_bytes / (1024 * 1024), 2),
            "legacy_traced_peak_bytes": legacy_peak_bytes,
            "legacy_traced_peak_mib": round(legacy_peak_bytes / (1024 * 1024), 2),
            "traced_memory_reduction_pct": round((1.0 - bounded_peak_bytes / legacy_peak_bytes) * 100.0, 2),
            "bounded_elapsed_ms": round(bounded_elapsed_ms, 2),
            "legacy_elapsed_ms": round(legacy_elapsed_ms, 2),
        },
    )


def test_real_postgres_execution_and_legacy_string_payload_parity():
    """AC1 & AC2: When TEST_DATABASE_URL is set, verifies PostgresRegistryStore SQL
    execution against real PostgreSQL, including legacy JSON-string wrapped rows
    and hosted-scale 9,148 strategy_spec memory reduction."""
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres execution test")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = f"registry_regime_eval_{uuid4().hex[:10]}"
    mem_store = RegistryStore()
    entries = _seed_parity_population(mem_store)
    pg_store = PostgresRegistryStore(
        dsn=dsn,
        entries_table=f"{schema}.entries",
        receipts_table=f"{schema}.command_receipts",
    )
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for i, entry in enumerate(entries):
                    doc = json.dumps(entry.to_dict(), sort_keys=True)
                    # Wrap every 2nd row as a legacy JSON-string payload to prove SQL unwrapping
                    payload_json = json.dumps(doc) if i % 2 == 1 else doc
                    cur.execute(
                        f'INSERT INTO "{schema}"."entries" (record_id, payload) VALUES (%s, %s::jsonb)',
                        (entry.registry_id, payload_json),
                    )
            conn.commit()

        for auth_header, x_tenant_id in (
            (_auth_headers("tenant-a")["Authorization"], None),
            (_auth_headers("tenant-b", sub="op-b")["Authorization"], None),
            (_auth_headers("tenant-a", roles=("admin",), sub="admin-1")["Authorization"], None),
            (_auth_headers("tenant-a", roles=("admin",), sub="admin-1")["Authorization"], "tenant-a"),
        ):
            ctx, header_tenant = svc._strategy_spec_read_scope(auth_header, x_tenant_id)
            visible_tenants = svc._persona_binding_visible_tenants(ctx, header_tenant)

            mem_strategies = RegistryService(mem_store).list_regime_evaluation_strategies(
                visible_tenants=visible_tenants
            )
            pg_strategies = RegistryService(pg_store).list_regime_evaluation_strategies(
                visible_tenants=visible_tenants
            )
            assert pg_strategies == mem_strategies

        assert RegistryService(pg_store).list_regime_model_artifact_rows() == (
            RegistryService(mem_store).list_regime_model_artifact_rows()
        )

        # Hosted-scale real-Postgres benchmark: 9,148 strategy_spec rows + 500 execution_bundle + 200 model_artifact
        with psycopg.connect(dsn) as conn:
            conn.execute(f'TRUNCATE "{schema}"."entries"')
            spec_payload = _realistic_strategy_spec_payload("strat-pg-bench", version="1.0.0", lifecycle_state="draft")
            base_dict = RegistryEntry(
                registry_id="reg-pg-bench",
                artifact_type=ArtifactType.STRATEGY_SPEC,
                strategy_id="strat-pg-bench",
                version="1.0.0",
                artifact_state=ArtifactState.DRAFT,
                lineage=Lineage(source_run_ids=["run-pg-bench"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
                checksum="sha256:" + "b" * 64,
                evaluation_summary={"sharpe": 1.72, "risk_level": "medium"},
                metadata={"strategy_spec": spec_payload, "tenant_id": "tenant-a"},
                created_at="2026-11-01T00:00:00Z",
                updated_at="2026-11-01T00:00:00Z",
                owner_tenant="tenant-a",
            ).to_dict()
            schedule = (
                [(ArtifactState.DRAFT.value, 7421)]
                + [(ArtifactState.APPROVED.value, 170)]
                + [(ArtifactState.CANDIDATE.value, 1)]
                + [(ArtifactState.RETIRED.value, 1556)]
            )
            with conn.cursor() as cur:
                with cur.copy(f'COPY "{schema}"."entries" (record_id, payload) FROM STDIN') as copy:
                    n = 0
                    for st, cnt in schedule:
                        for _ in range(cnt):
                            n += 1
                            row_d = dict(
                                base_dict,
                                registry_id=f"reg-pg-{n:05d}",
                                strategy_id=f"strat-pg-{n:05d}",
                                artifact_state=st,
                            )
                            raw_doc = json.dumps(row_d)
                            copy.write_row((row_d["registry_id"], json.dumps(raw_doc) if n % 1000 == 3 else raw_doc))
                    for m_idx in range(200):
                        m_d = dict(
                            base_dict,
                            registry_id=f"reg-pg-model-{m_idx:04d}",
                            strategy_id=f"model-pg-{m_idx:04d}",
                            artifact_type=ArtifactType.MODEL_ARTIFACT.value,
                            artifact_state=ArtifactState.DRAFT.value,
                            metadata={"blob": "x" * 20000},
                        )
                        copy.write_row((m_d["registry_id"], json.dumps(m_d)))
            conn.execute(f'ANALYZE "{schema}"."entries"')
            conn.commit()

        vis = frozenset({"tenant-a", BUILTIN_TENANT})
        pg_svc = RegistryService(pg_store)

        tracemalloc.start()
        tracemalloc.reset_peak()
        b0 = tracemalloc.get_traced_memory()[0]
        t_pg0 = time.perf_counter()
        pg_bounded_strategies = pg_svc.list_regime_evaluation_strategies(visible_tenants=vis)
        pg_bounded_models = pg_svc.list_regime_model_artifact_rows()
        pg_bounded_ms = (time.perf_counter() - t_pg0) * 1000.0
        pg_bounded_peak = tracemalloc.get_traced_memory()[1] - b0

        tracemalloc.reset_peak()
        b1 = tracemalloc.get_traced_memory()[0]
        t_pg1 = time.perf_counter()
        ctx_a, _ = svc._strategy_spec_read_scope(_auth_headers("tenant-a")["Authorization"], None)
        pg_legacy_strategies, _ = _legacy_select_strategies_and_models(pg_store, ctx_a, None)
        pg_legacy_ms = (time.perf_counter() - t_pg1) * 1000.0
        pg_legacy_peak = tracemalloc.get_traced_memory()[1] - b1
        tracemalloc.stop()

        assert len(pg_bounded_strategies) == len(pg_legacy_strategies) == 7592
        assert {
            (s["strategy_id"], s["registry_id"], s["owner_tenant"], profile_for(s["metadata"]))
            for s in pg_bounded_strategies
        } == {
            (s["strategy_id"], s["registry_id"], s["owner_tenant"], profile_for(s["metadata"]))
            for s in pg_legacy_strategies
        }
        assert len(pg_bounded_models) == 200
        assert all(m["metadata"] == {} for m in pg_bounded_models)
        assert pg_bounded_peak < 16 * 1024 * 1024
        assert pg_bounded_peak < pg_legacy_peak * 0.15

        _record_evidence(
            "real_postgres_benchmark_9148_specs",
            {
                "total_rows_in_table": 9348,
                "strategy_spec_rows": 9148,
                "non_retired_strategy_spec_rows": 7592,
                "retired_strategy_spec_rows": 1556,
                "unrelated_20kb_model_artifact_rows": 200,
                "legacy_string_wrapped_rows": 9,
                "bounded_traced_peak_bytes": pg_bounded_peak,
                "bounded_traced_peak_mib": round(pg_bounded_peak / (1024 * 1024), 2),
                "legacy_traced_peak_bytes": pg_legacy_peak,
                "legacy_traced_peak_mib": round(pg_legacy_peak / (1024 * 1024), 2),
                "traced_memory_reduction_pct": round((1.0 - pg_bounded_peak / pg_legacy_peak) * 100.0, 2),
                "bounded_elapsed_ms": round(pg_bounded_ms, 2),
                "legacy_elapsed_ms": round(pg_legacy_ms, 2),
            },
        )
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
