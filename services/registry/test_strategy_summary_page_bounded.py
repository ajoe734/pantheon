"""Bounded projection, parity, CPU reduction, and error-contract tests for
``GET /api/registry/strategy-specs?view=strategy_summary_page``
(REGISTRY-STRATEGY-SUMMARY-BOUNDED-20261110).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Iterator, Mapping

import psycopg
import pytest

from .models import (
    BUILTIN_TENANT,
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from .pg_store import (
    _STRATEGY_SUMMARY_CONTAINER_PATHS,
    _STRATEGY_SUMMARY_INDEX_CONTAINER_PATHS,
    _strategy_summary_index_leaf_paths,
    _strategy_summary_leaf_paths,
    PostgresRegistryStore,
    strategy_spec_summary_index_rows_from_columns,
    strategy_spec_summary_index_select_sql,
    strategy_spec_summary_rows_from_columns,
    strategy_spec_summary_select_sql,
)
from .service import RegistryError, RegistryService
from .storage import (
    RegistryStore,
    project_strategy_spec_summary_page,
    strategy_spec_summary_row,
)
from .test_service import _bearer, _jwt, _valid_spec, strict_client  # noqa: F401


_HEAVY_SUMMARY_SPEC_KEYS = frozenset(
    {
        "title",
        "name",
        "summary",
        "description",
        "market",
        "strategy_family",
        "rebalance_frequency",
        "DecisionCadence",
        "decision_cadence",
        "risk",
        "risk_level",
        "owner",
        "author",
        "created_by",
        "source_kind",
        "capital_pool_id",
        "capitalPoolId",
        "deployment_plan_id",
        "runtime_binding_id",
        "source_seed_id",
        "governance",
        "market_scope",
        "execution_profile",
        "universe",
        "execution_assumptions",
        "risk_profile",
        "evaluation_summary",
    }
)


class _SpyMapping(dict):
    """Dict subclass that records which rows had any heavy summary leaf read."""

    def __init__(
        self,
        data: Mapping[str, Any],
        *,
        registry_id: str,
        heavy_keys: frozenset[str],
        heavy_reads_by_row: set[str],
    ) -> None:
        super().__init__(data)
        self._registry_id = registry_id
        self._heavy_keys = heavy_keys
        self._heavy_reads_by_row = heavy_reads_by_row

    def __getitem__(self, key: str) -> Any:
        if key in self._heavy_keys:
            self._heavy_reads_by_row.add(self._registry_id)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._heavy_keys:
            self._heavy_reads_by_row.add(self._registry_id)
        return super().get(key, default)


def _realistic_strategy_spec_payload(
    strategy_id: str,
    *,
    version: str = "1.0.0",
    lifecycle_state: str = "draft",
    persona_ids: list[str] | None = None,
    title_prefix: str = "TW Institutional Momentum",
) -> dict[str, Any]:
    symbols = [f"{2300 + j}.TW" for j in range(30)]
    spec: dict[str, Any] = {
        "strategy_id": strategy_id,
        "version": version,
        "spec_version": version,
        "title": f"{title_prefix} {strategy_id}",
        "name": f"{title_prefix} {strategy_id}",
        "summary": f"Bounded summary test payload for {strategy_id} ({'x' * 320})",
        "description": f"Detailed institutional hypothesis for {strategy_id}: {'y' * 640}",
        "lifecycle_state": lifecycle_state,
        "market": "TW",
        "strategy_family": "Cross-Sectional Momentum",
        "rebalance_frequency": "weekly",
        "decision_cadence": "daily",
        "risk": "medium",
        "risk_level": "medium",
        "owner": "quant-research-desk",
        "created_by": "research-agent-tw",
        "source_kind": "research_factory",
        "capital_pool_id": f"pool-{strategy_id}",
        "capitalPoolId": f"pool-{strategy_id}",
        "source_seed_id": f"seed-{strategy_id}",
        "governance": {
            "risk_level": "medium",
            "risk": "medium",
            "owner": "quant-research-desk",
            "capital_pool_id": f"pool-{strategy_id}",
            "capitalPoolId": f"pool-{strategy_id}",
        },
        "market_scope": {
            "market": "TW",
            "alpha": "cross_sectional_momentum",
            "frequency": "daily",
            "symbols": symbols,
            "asset_class": "Equities",
            "region": "TW",
            "sub_universe": "TW50_MID100",
            "benchmark": "TAIEX",
        },
        "execution_profile": {
            "capital_pool_id": f"pool-{strategy_id}",
            "capitalPoolId": f"pool-{strategy_id}",
            "risk_level": "medium",
            "execution_mode_hint": "paper",
        },
        "universe": {
            "name": "TW50 + MidCap 100 Liquid",
            "exchange": "TWSE",
            "currency": "TWD",
            "SelectionRule": "top_150_by_adv_twd",
            "symbols": symbols,
            "filters": [{"field": "adv_20d_twd", "op": ">=", "value": 50_000_000}],
        },
        "execution_assumptions": {
            "DecisionCadence": "daily",
            "rebalance_cadence": "weekly",
            "execution_model": "twap_30m",
            "slippage_bps": 8.0,
            "commission_bps": 4.275,
        },
        "risk_profile": {
            "risk_level": "medium",
            "max_drawdown": 0.15,
            "max_position_pct": 0.08,
            "gross_leverage_limit": 1.0,
            "stop_loss_pct": 0.06,
        },
        "evaluation_summary": {
            "sharpe": 1.72,
            "cagr": 0.214,
            "win_rate": 0.568,
            "max_drawdown": -0.112,
            "profit_factor": 1.54,
            "trades_count": 384,
            "backtest_period": "2020-01-01..2025-12-31",
        },
        "provenance": {
            "origin_type": "research_factory",
            "source_kind": "research_factory",
            "created_by": "research-agent-tw",
            "created_at": "2026-10-01T00:00:00Z",
            "source_seed_id": f"seed-{strategy_id}",
            "generator": "pantheon-spec-distiller-v2",
        },
        "metadata": {
            "tenant_id": "tenant-dev",
            "capital_pool_id": f"pool-{strategy_id}",
            "capitalPoolId": f"pool-{strategy_id}",
            "owner": "quant-research-desk",
            "risk": "medium",
            "lifecycle_state": lifecycle_state,
        },
        "features": [
            {"feature_id": f"feat_mom_{k}", "lookback_days": 20 * (k + 1), "weight": 0.25}
            for k in range(8)
        ],
        "signals": [
            {"signal_id": f"sig_{k}", "expr": f"rank(feat_mom_{k}) > 0.7", "notes": "z" * 180}
            for k in range(6)
        ],
    }
    if persona_ids is not None:
        spec["persona_ids"] = list(persona_ids)
    return spec


def test_strategy_summary_page_extracts_full_leaves_only_for_returned_page_and_persona_filter_runs_before_extraction():
    """AC1: A ``strategy_summary_page`` read extracts the full summary fields
    only for the rows it returns, and a ``persona_id`` filter is applied before
    any full field extraction. Proven both by ``store.full_summary_rows_extracted``
    and by a per-row mapping spy on the heavy summary leaf keys.
    """
    store = RegistryStore()
    heavy_reads_by_row: set[str] = set()

    # Seed 120 active strategy families (with multi-version families = 150 rows),
    # plus 15 retired families, 5 foreign-tenant families, and 3 model_artifact rows.
    for i in range(120):
        sid = f"strat-bounded-{i:04d}"
        persona = "persona-target-single" if i == 42 else f"persona-group-{i % 12}"
        # Multi-version family every 4th strategy
        if i % 4 == 0:
            rid_v1 = f"reg-{sid}-v1"
            spec_v1 = _SpyMapping(
                _realistic_strategy_spec_payload(
                    sid, version="1.0.0", lifecycle_state="draft", persona_ids=["persona-superseded"]
                ),
                registry_id=rid_v1,
                heavy_keys=_HEAVY_SUMMARY_SPEC_KEYS,
                heavy_reads_by_row=heavy_reads_by_row,
            )
            store._entries[rid_v1] = RegistryEntry(
                registry_id=rid_v1,
                artifact_type=ArtifactType.STRATEGY_SPEC,
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.DRAFT,
                lineage=Lineage(source_run_ids=["run-v1"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
                checksum=f"sha256:{rid_v1}",
                metadata={"strategy_spec": spec_v1},
                created_at=f"2026-10-{(i % 28) + 1:02d}T00:00:00Z",
                updated_at=f"2026-10-{(i % 28) + 1:02d}T01:00:00Z",
                owner_tenant="tenant-a",
            )

        rid_cur = f"reg-{sid}-v2"
        state = ArtifactState.APPROVED if i % 3 == 0 else (ArtifactState.CANDIDATE if i % 3 == 1 else ArtifactState.DRAFT)
        spec_cur = _SpyMapping(
            _realistic_strategy_spec_payload(
                sid, version="1.1.0", lifecycle_state=state.value, persona_ids=[persona]
            ),
            registry_id=rid_cur,
            heavy_keys=_HEAVY_SUMMARY_SPEC_KEYS,
            heavy_reads_by_row=heavy_reads_by_row,
        )
        store._entries[rid_cur] = RegistryEntry(
            registry_id=rid_cur,
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version="1.1.0",
            artifact_state=state,
            lineage=Lineage(source_run_ids=["run-v2"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{rid_cur}",
            metadata={"strategy_spec": spec_cur},
            created_at=f"2026-10-{(i % 28) + 1:02d}T02:00:00Z",
            updated_at=f"2026-10-{(i % 28) + 1:02d}T03:00:00Z",
            owner_tenant="tenant-a",
        )

    for i in range(15):
        sid = f"strat-retired-{i:04d}"
        rid = f"reg-{sid}-v1"
        spec_ret = _SpyMapping(
            _realistic_strategy_spec_payload(sid, version="1.0.0", lifecycle_state="retired", persona_ids=["persona-retired"]),
            registry_id=rid,
            heavy_keys=_HEAVY_SUMMARY_SPEC_KEYS,
            heavy_reads_by_row=heavy_reads_by_row,
        )
        store._entries[rid] = RegistryEntry(
            registry_id=rid,
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version="1.0.0",
            artifact_state=ArtifactState.RETIRED,
            lineage=Lineage(source_run_ids=["run-ret"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{rid}",
            metadata={"strategy_spec": spec_ret},
            created_at="2026-10-01T00:00:00Z",
            updated_at="2026-10-01T00:00:00Z",
            owner_tenant="tenant-a",
        )

    svc = RegistryService(store)
    visible = frozenset({"tenant-a", BUILTIN_TENANT})

    # 1. Default page with page_size=5 -> only 5 rows have full summary leaves read
    heavy_reads_by_row.clear()
    page_5 = svc.strategy_spec_summary_page(visible_tenants=visible, page_size=5)
    assert page_5["total"] == 120
    assert len(page_5["items"]) == 5
    expected_ids_5 = tuple(item["current_spec_version_id"] for item in page_5["items"])
    assert store.full_summary_rows_extracted == 5
    assert store.full_summary_row_ids_extracted == expected_ids_5
    assert heavy_reads_by_row == set(expected_ids_5)

    # 2. Default page with page_size=20 -> only 20 rows have full summary leaves read
    heavy_reads_by_row.clear()
    page_20 = svc.strategy_spec_summary_page(visible_tenants=visible, page_size=20)
    assert page_20["total"] == 120
    assert len(page_20["items"]) == 20
    expected_ids_20 = tuple(item["current_spec_version_id"] for item in page_20["items"])
    assert store.full_summary_rows_extracted == 20
    assert store.full_summary_row_ids_extracted == expected_ids_20
    assert heavy_reads_by_row == set(expected_ids_20)

    # 3. Persona filter matching a single strategy -> persona filter runs BEFORE full field extraction,
    #    so only 1 row has its full summary leaves read across all 165 stored rows.
    heavy_reads_by_row.clear()
    page_persona = svc.strategy_spec_summary_page(
        visible_tenants=visible,
        persona_id="persona-target-single",
        page_size=20,
    )
    assert page_persona["total"] == 1
    assert len(page_persona["items"]) == 1
    assert page_persona["items"][0]["strategy_id"] == "strat-bounded-0042"
    assert store.full_summary_rows_extracted == 1
    assert store.full_summary_row_ids_extracted == ("reg-strat-bounded-0042-v2",)
    assert heavy_reads_by_row == {"reg-strat-bounded-0042-v2"}

    # 4. Persona filter from a superseded v1 ("persona-superseded") -> 0 matches, 0 full rows read
    heavy_reads_by_row.clear()
    page_superseded = svc.strategy_spec_summary_page(
        visible_tenants=visible,
        persona_id="persona-superseded",
        page_size=20,
    )
    assert page_superseded["total"] == 0
    assert page_superseded["items"] == []
    assert store.full_summary_rows_extracted == 0
    assert store.full_summary_row_ids_extracted == ()
    assert heavy_reads_by_row == set()


def test_postgres_registry_store_two_phase_summary_page_queries_only_returned_ids():
    """AC1 (Postgres store unit proof): ``PostgresRegistryStore`` Phase 1
    queries only index columns (never heavy summary leaves), and Phase 2
    fetches full summary columns only for the ``registry_id``s in the final
    sliced page.
    """
    index_sql = strategy_spec_summary_index_select_sql()
    full_sql = strategy_spec_summary_select_sql()
    for heavy_leaf in ("market_scope", "execution_profile", "governance", "title", "capital_pool_id", "source_kind"):
        assert heavy_leaf not in index_sql
        assert heavy_leaf in full_sql

    entries: list[RegistryEntry] = []
    for i in range(10):
        sid = f"strat-pg-unit-{i:02d}"
        rid = f"reg-{sid}-v1"
        persona = "persona-pg-special" if i == 7 else "persona-pg-common"
        entries.append(
            RegistryEntry(
                registry_id=rid,
                artifact_type=ArtifactType.STRATEGY_SPEC,
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.APPROVED,
                lineage=Lineage(source_run_ids=["run-pg"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
                checksum=f"sha256:{rid}",
                metadata={
                    "strategy_spec": _realistic_strategy_spec_payload(
                        sid, version="1.0.0", lifecycle_state="approved", persona_ids=[persona]
                    )
                },
                created_at=f"2026-10-{i + 1:02d}T00:00:00Z",
                updated_at=f"2026-10-{i + 1:02d}T01:00:00Z",
                owner_tenant="tenant-a",
            )
        )

    def _simulate_sql_row(entry: RegistryEntry, leaf_paths: tuple[tuple[str, ...], ...], container_paths: tuple[tuple[str, ...], ...]) -> tuple[Any, ...]:
        payload = entry.to_dict()

        def _extract(p: tuple[str, ...]):
            cur: Any = payload
            for k in p:
                if not isinstance(cur, dict) or k not in cur:
                    return None
                cur = cur[k]
            return json.dumps(cur)

        def _typeof(p: tuple[str, ...]):
            cur: Any = payload
            for k in p:
                if not isinstance(cur, dict) or k not in cur:
                    return None
                cur = cur[k]
            return "object" if isinstance(cur, dict) else ("array" if isinstance(cur, list) else "scalar")

        return tuple(_extract(p) for p in leaf_paths) + tuple(_typeof(p) for p in container_paths)

    class _FakeEntriesTable:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, tuple]] = []

        def list_all_selected(
            self,
            select_sql: str,
            *,
            visible_sql: str = "TRUE",
            family_sql: str | None = None,
            bound_sql: str | None = None,
            keep_params: tuple = (),
            conn=None,
        ):
            self.calls.append((select_sql, visible_sql, keep_params))
            if select_sql == index_sql:
                return [
                    _simulate_sql_row(e, _strategy_summary_index_leaf_paths(), _STRATEGY_SUMMARY_INDEX_CONTAINER_PATHS)
                    for e in entries
                ]
            if select_sql == full_sql:
                requested_ids = set(keep_params[-1])
                return [
                    _simulate_sql_row(e, _strategy_summary_leaf_paths(), _STRATEGY_SUMMARY_CONTAINER_PATHS)
                    for e in entries
                    if e.registry_id in requested_ids
                ]
            raise AssertionError(f"Unexpected select_sql: {select_sql}")

    pg_store = PostgresRegistryStore.__new__(PostgresRegistryStore)
    fake_table = _FakeEntriesTable()
    pg_store._entries = fake_table
    pg_store.full_summary_rows_extracted = 0
    pg_store.full_summary_row_ids_extracted = ()

    svc = RegistryService(pg_store)
    page = svc.strategy_spec_summary_page(
        visible_tenants=frozenset({"tenant-a", BUILTIN_TENANT}),
        persona_id="persona-pg-special",
        page_size=20,
    )
    assert page["total"] == 1
    assert [item["strategy_id"] for item in page["items"]] == ["strat-pg-unit-07"]
    assert pg_store.full_summary_rows_extracted == 1
    assert pg_store.full_summary_row_ids_extracted == ("reg-strat-pg-unit-07-v1",)
    assert len(fake_table.calls) == 2
    assert fake_table.calls[0][0] == index_sql
    assert fake_table.calls[1][0] == full_sql
    assert fake_table.calls[1][2][-1] == ["reg-strat-pg-unit-07-v1"]


def test_strategy_summary_page_parity_with_legacy_across_real_row_shapes():
    """AC2: Every response of the view stays identical to the previous
    implementation including ``items``, ``total``, ``next_page_token``,
    ``page_info``, and ``state_counts`` for the real row shapes with:
    - multiple versions (semver ordering ``1.0.0`` < ``1.2.0`` < ``1.10.0``,
      non-semver tags, timestamp tie-breaking, retired older version vs retired
      latest version),
    - retired, draft, candidate/review, approved, paper/canary/live running,
      deployed, paused, and unknown lifecycle values,
    - persona IDs from all 5 metadata sources (``strategy_spec.persona_ids``,
      ``metadata.personaIds``, ``metadata.persona_ids``, ``metadata.persona_id``,
      ``strategy_spec.metadata.persona_id``),
    - tenant visibility (``tenant-a``, ``tenant-b``, ``__builtin__``, and admin).
    """
    store = RegistryStore()

    def _add(
        rid: str,
        sid: str,
        version: str,
        artifact_state: ArtifactState,
        tenant: str,
        *,
        metadata: dict[str, Any],
        created_at: str = "2026-10-10T00:00:00Z",
        updated_at: str = "2026-10-10T01:00:00Z",
        evaluation_summary: dict[str, Any] | None = None,
    ) -> None:
        store._entries[rid] = RegistryEntry(
            registry_id=rid,
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version=version,
            artifact_state=artifact_state,
            lineage=Lineage(source_run_ids=[f"run-{rid}"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{rid}",
            metadata=metadata,
            evaluation_summary=evaluation_summary,
            created_at=created_at,
            updated_at=updated_at,
            owner_tenant=tenant,
        )

    # 1. Multi-version family with semver 1.0.0 -> 1.2.0 -> 1.10.0 and older retired version
    _add(
        "reg-mv-v0",
        "strat-multi-semver",
        "0.9.0",
        ArtifactState.RETIRED,
        "tenant-a",
        metadata={"strategy_spec": _realistic_strategy_spec_payload("strat-multi-semver", version="0.9.0", lifecycle_state="retired", persona_ids=["p-retired-old"])},
        updated_at="2026-10-01T00:00:00Z",
    )
    _add(
        "reg-mv-v1",
        "strat-multi-semver",
        "1.0.0",
        ArtifactState.DRAFT,
        "tenant-a",
        metadata={"strategy_spec": _realistic_strategy_spec_payload("strat-multi-semver", version="1.0.0", lifecycle_state="draft", persona_ids=["p-old"])},
        updated_at="2026-10-02T00:00:00Z",
    )
    _add(
        "reg-mv-v2",
        "strat-multi-semver",
        "1.2.0",
        ArtifactState.CANDIDATE,
        "tenant-a",
        metadata={"strategy_spec": _realistic_strategy_spec_payload("strat-multi-semver", version="1.2.0", lifecycle_state="candidate", persona_ids=["p-mid"])},
        updated_at="2026-10-03T00:00:00Z",
    )
    _add(
        "reg-mv-v10",
        "strat-multi-semver",
        "1.10.0",
        ArtifactState.APPROVED,
        "tenant-a",
        metadata={
            "strategy_spec": _realistic_strategy_spec_payload(
                "strat-multi-semver", version="1.10.0", lifecycle_state="paper_running", persona_ids=["p-src-spec-list", "p-shared"]
            ),
            "personaIds": ["p-src-meta-camel", "p-shared"],
            "persona_ids": ["p-src-meta-snake"],
            "persona_id": "p-src-meta-single",
        },
        updated_at="2026-10-02T12:00:00Z",
    )

    # 2. Persona source 5: strategy_spec.metadata.persona_id + candidate state
    spec_nested_persona = _realistic_strategy_spec_payload(
        "strat-nested-persona", version="1.0.0", lifecycle_state="candidate", persona_ids=None
    )
    spec_nested_persona["metadata"] = {"persona_id": "p-src-spec-nested-single"}
    _add(
        "reg-nested-persona",
        "strat-nested-persona",
        "1.0.0",
        ArtifactState.CANDIDATE,
        "tenant-a",
        metadata={"strategy_spec": spec_nested_persona},
        updated_at="2026-10-11T00:00:00Z",
    )

    # 3. Unknown lifecycle states (both on DRAFT and APPROVED artifact_state)
    _add(
        "reg-unknown-draft",
        "strat-unknown-draft",
        "1.0.0",
        ArtifactState.DRAFT,
        "tenant-a",
        metadata={
            "strategy_spec": _realistic_strategy_spec_payload(
                "strat-unknown-draft", version="1.0.0", lifecycle_state="experimental_unknown", persona_ids=["p-unknown"]
            )
        },
        updated_at="2026-10-12T00:00:00Z",
    )
    _add(
        "reg-unknown-meta",
        "strat-unknown-meta",
        "1.0.0",
        ArtifactState.APPROVED,
        "tenant-a",
        metadata={
            "lifecycle_state": "shadow_staging_custom",
            "persona_id": "p-meta-only",
            "strategy_spec": {
                "strategy_id": "strat-unknown-meta",
                "title": "Meta lifecycle fallback",
            },
        },
        evaluation_summary={"sharpe": 2.05, "cagr": 0.31, "max_drawdown": -0.09},
        updated_at="2026-10-13T00:00:00Z",
    )

    # 4. Various active lifecycle states: draft, approved, canary_running, live_running, deployed, paused
    for idx, (st_name, art_state) in enumerate(
        [
            ("draft", ArtifactState.DRAFT),
            ("approved", ArtifactState.APPROVED),
            ("canary_running", ArtifactState.APPROVED),
            ("live_running", ArtifactState.APPROVED),
            ("deployed", ArtifactState.APPROVED),
            ("paused", ArtifactState.APPROVED),
        ]
    ):
        sid = f"strat-state-{st_name}"
        _add(
            f"reg-state-{st_name}",
            sid,
            "1.0.0",
            art_state,
            "tenant-a",
            metadata={
                "strategy_spec": _realistic_strategy_spec_payload(
                    sid, version="1.0.0", lifecycle_state=st_name, persona_ids=[f"p-{st_name}", "p-shared"]
                )
            },
            updated_at=f"2026-10-{14 + idx:02d}T00:00:00Z",
        )

    # 5. Family whose latest version is RETIRED (even though v1 was APPROVED)
    _add(
        "reg-ret-family-v1",
        "strat-retired-family",
        "1.0.0",
        ArtifactState.APPROVED,
        "tenant-a",
        metadata={"strategy_spec": _realistic_strategy_spec_payload("strat-retired-family", version="1.0.0", lifecycle_state="approved", persona_ids=["p-shared"])},
        updated_at="2026-10-05T00:00:00Z",
    )
    _add(
        "reg-ret-family-v2",
        "strat-retired-family",
        "2.0.0",
        ArtifactState.RETIRED,
        "tenant-a",
        metadata={"strategy_spec": _realistic_strategy_spec_payload("strat-retired-family", version="2.0.0", lifecycle_state="retired", persona_ids=["p-shared"])},
        updated_at="2026-10-20T00:00:00Z",
    )

    # 6. Builtin strategy visible to all tenants + foreign tenant-b strategy
    _add(
        "reg-builtin-1",
        "strat-builtin-global",
        "1.0.0",
        ArtifactState.APPROVED,
        BUILTIN_TENANT,
        metadata={
            "strategy_spec": _realistic_strategy_spec_payload(
                "strat-builtin-global", version="1.0.0", lifecycle_state="approved", persona_ids=["p-builtin", "p-shared"]
            )
        },
        updated_at="2026-10-21T00:00:00Z",
    )
    _add(
        "reg-tenant-b-1",
        "strat-tenant-b-private",
        "1.0.0",
        ArtifactState.APPROVED,
        "tenant-b",
        metadata={
            "strategy_spec": _realistic_strategy_spec_payload(
                "strat-tenant-b-private", version="1.0.0", lifecycle_state="live_running", persona_ids=["p-tenant-b", "p-shared"]
            )
        },
        updated_at="2026-10-22T00:00:00Z",
    )

    svc = RegistryService(store)

    query_cases: list[dict[str, Any]] = [
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "page_size": 3, "page_token": None},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "page_size": 3, "page_token": "3"},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "page_size": 3, "page_token": "6"},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "page_size": 3, "page_token": "9"},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "page_size": 3, "page_token": "50"},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "exclude_state": "draft", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "draft", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "review", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "candidate", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "approved", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "paper_running", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "canary_running", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "live_running", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "deployed", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "paused", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "retired", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "state": "experimental_unknown", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-src-spec-list", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-src-meta-camel", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-src-meta-snake", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-src-meta-single", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-src-spec-nested-single", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-meta-only", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-old", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-shared", "state": "approved", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-a", BUILTIN_TENANT}), "persona_id": "p-shared", "exclude_state": "draft", "page_size": 20},
        {"visible_tenants": frozenset({"tenant-b", BUILTIN_TENANT}), "page_size": 20},
        {"visible_tenants": None, "page_size": 20},
    ]

    for kwargs in query_cases:
        visible = kwargs.get("visible_tenants")
        # Previous implementation: extract full summary rows for every visible entry, then project
        legacy_full_rows = [
            strategy_spec_summary_row(entry)
            for entry in store._entries.values()
            if entry.artifact_type == ArtifactType.STRATEGY_SPEC
            and (visible is None or entry.owner_tenant in visible)
        ]
        expected = project_strategy_spec_summary_page(
            legacy_full_rows,
            state=kwargs.get("state"),
            exclude_state=kwargs.get("exclude_state"),
            persona_id=kwargs.get("persona_id"),
            page_size=kwargs.get("page_size", 50),
            page_token=kwargs.get("page_token"),
            full_row_loader=None,
        )

        # New implementation: two-phase bounded projection
        actual = svc.strategy_spec_summary_page(**kwargs)
        assert actual == expected, f"Mismatch for query {kwargs}"
        assert store.full_summary_rows_extracted == len(actual["items"]), f"Unbounded extraction for {kwargs}"


def test_strategy_summary_page_cpu_reduction_over_3400_real_shape_strategy_families():
    """AC4: Over ~3,400 strategy families of the real payload shape, CPU for
    (1) one persona-filtered call and (2) one default first page each drop by
    at least 70 percent compared to full-row extraction on the same machine.
    When ``TEST_DATABASE_URL`` is set, also measures Postgres + Registry CPU
    end-to-end against real PostgreSQL and proves >= 70% reduction.
    """
    # Build 3,400 current non-retired families + 600 retired families (4,150 total rows)
    entries: list[RegistryEntry] = []
    for i in range(3400):
        sid = f"strat-perf-{i:04d}"
        persona = "persona-perf-target" if i == 1700 else f"persona-perf-{i % 40}"
        state = (
            ArtifactState.DRAFT
            if i < 3200
            else (ArtifactState.CANDIDATE if i < 3300 else ArtifactState.APPROVED)
        )
        def _entry_metadata(spec_dict: dict[str, Any], rid_val: str) -> dict[str, Any]:
            return {
                "spec_version_id": rid_val,
                "tenant_id": "tenant-dev",
                "spec_version": spec_dict["version"],
                "version": spec_dict["version"],
                "title": spec_dict["title"],
                "name": spec_dict["name"],
                "risk": "medium",
                "owner": "quant-research-desk",
                "requested_by": "research-agent-tw",
                "capital_pool_id": spec_dict["capital_pool_id"],
                "capitalPoolId": spec_dict["capital_pool_id"],
                "source_kind": "research_factory",
                "provenance": spec_dict["provenance"],
                "governance": spec_dict["governance"],
                "market_scope": spec_dict["market_scope"],
                "execution_profile": spec_dict["execution_profile"],
                "strategy_spec": spec_dict,
            }

        if i % 25 == 0:
            rid_v1 = f"reg-{sid}-v1"
            spec_v1 = _realistic_strategy_spec_payload(
                sid, version="1.0.0", lifecycle_state="draft", persona_ids=[persona]
            )
            entries.append(
                RegistryEntry(
                    registry_id=rid_v1,
                    artifact_type=ArtifactType.STRATEGY_SPEC,
                    strategy_id=sid,
                    version="1.0.0",
                    artifact_state=ArtifactState.DRAFT,
                    lineage=Lineage(source_run_ids=["run-perf-v1"]),
                    storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
                    checksum=f"sha256:{rid_v1}",
                    evaluation_summary={"risk_level": "medium", "sharpe": 1.72},
                    metadata=_entry_metadata(spec_v1, rid_v1),
                    created_at="2026-10-01T00:00:00Z",
                    updated_at="2026-10-01T00:00:00Z",
                    owner_tenant="tenant-dev",
                )
            )
        rid = f"reg-{sid}-v2"
        spec_v2 = _realistic_strategy_spec_payload(
            sid, version="1.1.0", lifecycle_state=state.value, persona_ids=[persona]
        )
        entries.append(
            RegistryEntry(
                registry_id=rid,
                artifact_type=ArtifactType.STRATEGY_SPEC,
                strategy_id=sid,
                version="1.1.0",
                artifact_state=state,
                lineage=Lineage(source_run_ids=["run-perf-v2"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
                checksum=f"sha256:{rid}",
                evaluation_summary={"risk_level": "medium", "sharpe": 1.72},
                metadata=_entry_metadata(spec_v2, rid),
                created_at="2026-10-02T00:00:00Z",
                updated_at=f"2026-10-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00Z",
                owner_tenant="tenant-dev",
            )
        )

    for i in range(600):
        sid = f"strat-perf-ret-{i:04d}"
        rid = f"reg-{sid}-v1"
        spec_ret = _realistic_strategy_spec_payload(
            sid, version="1.0.0", lifecycle_state="retired", persona_ids=["persona-retired"]
        )
        entries.append(
            RegistryEntry(
                registry_id=rid,
                artifact_type=ArtifactType.STRATEGY_SPEC,
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.RETIRED,
                lineage=Lineage(source_run_ids=["run-perf-ret"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
                checksum=f"sha256:{rid}",
                evaluation_summary={"risk_level": "medium", "sharpe": 1.10},
                metadata={
                    "spec_version_id": rid,
                    "tenant_id": "tenant-dev",
                    "title": spec_ret["title"],
                    "name": spec_ret["name"],
                    "risk": "medium",
                    "owner": "quant-research-desk",
                    "capital_pool_id": spec_ret["capital_pool_id"],
                    "provenance": spec_ret["provenance"],
                    "governance": spec_ret["governance"],
                    "market_scope": spec_ret["market_scope"],
                    "execution_profile": spec_ret["execution_profile"],
                    "strategy_spec": spec_ret,
                },
                created_at="2026-10-01T00:00:00Z",
                updated_at="2026-10-01T00:00:00Z",
                owner_tenant="tenant-dev",
            )
        )

    # Pre-build simulated SQL column tuples for all rows to benchmark the SQL column
    # decoder + projection path deterministically even when TEST_DATABASE_URL is unset.
    full_leaf_paths = _strategy_summary_leaf_paths()
    index_leaf_paths = _strategy_summary_index_leaf_paths()
    full_sql_tuples: list[tuple[Any, ...]] = []
    index_sql_tuples: list[tuple[Any, ...]] = []
    full_sql_by_id: dict[str, tuple[Any, ...]] = {}

    for e in entries:
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

        ft = tuple(_ext(path) for path in full_leaf_paths) + tuple(
            _typ(path) for path in _STRATEGY_SUMMARY_CONTAINER_PATHS
        )
        it = tuple(_ext(path) for path in index_leaf_paths) + tuple(
            _typ(path) for path in _STRATEGY_SUMMARY_INDEX_CONTAINER_PATHS
        )
        full_sql_tuples.append(ft)
        index_sql_tuples.append(it)
        full_sql_by_id[e.registry_id] = ft

    def _loader_from_sql_tuples(ids: list[str]) -> dict[str, dict[str, Any]]:
        rows = strategy_spec_summary_rows_from_columns([full_sql_by_id[rid] for rid in ids if rid in full_sql_by_id])
        return {str(r["registry_id"]): r for r in rows}

    # Warm caches once
    _ = project_strategy_spec_summary_page(
        strategy_spec_summary_index_rows_from_columns(index_sql_tuples),
        page_size=20,
        full_row_loader=_loader_from_sql_tuples,
    )

    # Measure legacy vs bounded CPU over 3 iterations
    t0 = time.process_time()
    for _ in range(3):
        legacy_default = project_strategy_spec_summary_page(
            strategy_spec_summary_rows_from_columns(full_sql_tuples),
            page_size=20,
        )
    legacy_default_cpu = (time.process_time() - t0) / 3.0

    t0 = time.process_time()
    for _ in range(3):
        bounded_default = project_strategy_spec_summary_page(
            strategy_spec_summary_index_rows_from_columns(index_sql_tuples),
            page_size=20,
            full_row_loader=_loader_from_sql_tuples,
        )
    bounded_default_cpu = (time.process_time() - t0) / 3.0

    t0 = time.process_time()
    for _ in range(3):
        legacy_persona = project_strategy_spec_summary_page(
            strategy_spec_summary_rows_from_columns(full_sql_tuples),
            persona_id="persona-perf-target",
            page_size=20,
        )
    legacy_persona_cpu = (time.process_time() - t0) / 3.0

    t0 = time.process_time()
    for _ in range(3):
        bounded_persona = project_strategy_spec_summary_page(
            strategy_spec_summary_index_rows_from_columns(index_sql_tuples),
            persona_id="persona-perf-target",
            page_size=20,
            full_row_loader=_loader_from_sql_tuples,
        )
    bounded_persona_cpu = (time.process_time() - t0) / 3.0

    assert bounded_default == legacy_default
    assert bounded_persona == legacy_persona
    default_drop_pct = (1.0 - (bounded_default_cpu / legacy_default_cpu)) * 100.0
    persona_drop_pct = (1.0 - (bounded_persona_cpu / legacy_persona_cpu)) * 100.0
    assert default_drop_pct >= 70.0, (
        f"Expected >= 70% CPU reduction for default first page, got {default_drop_pct:.1f}% "
        f"(legacy={legacy_default_cpu * 1000:.2f}ms, bounded={bounded_default_cpu * 1000:.2f}ms)"
    )
    assert persona_drop_pct >= 70.0, (
        f"Expected >= 70% CPU reduction for persona-filtered call, got {persona_drop_pct:.1f}% "
        f"(legacy={legacy_persona_cpu * 1000:.2f}ms, bounded={bounded_persona_cpu * 1000:.2f}ms)"
    )

    # If TEST_DATABASE_URL is configured, also measure real Postgres + Registry CPU end-to-end
    db_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if db_url:
        entries_table = f"registry_entries_perf_{uuid.uuid4().hex[:8]}"
        receipts_table = f"registry_receipts_perf_{uuid.uuid4().hex[:8]}"
        pg_store = PostgresRegistryStore(
            dsn=db_url,
            entries_table=entries_table,
            receipts_table=receipts_table,
        )
        try:
            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    with cur.copy(f"COPY {entries_table} (record_id, payload) FROM STDIN") as copy:
                        for e in entries:
                            copy.write_row((e.registry_id, json.dumps(e.to_dict())))
                conn.commit()
                with conn.cursor() as cur:
                    cur.execute(f"ANALYZE {entries_table}")
                conn.commit()

            visible = frozenset({"tenant-dev", BUILTIN_TENANT})
            pg_svc = RegistryService(pg_store)

            # Warm Postgres buffer cache
            _ = pg_svc.strategy_spec_summary_page(visible_tenants=visible, page_size=20)

            # Legacy end-to-end (Postgres full 94-leaf scan + Python projection)
            t_wall0 = time.perf_counter()
            for _ in range(3):
                pg_legacy_default = project_strategy_spec_summary_page(
                    pg_store.list_strategy_spec_summary_rows(
                        visible_tenants=visible, artifact_type="strategy_spec"
                    ),
                    page_size=20,
                )
            pg_legacy_default_ms = ((time.perf_counter() - t_wall0) / 3.0) * 1000.0

            t_wall0 = time.perf_counter()
            for _ in range(3):
                pg_bounded_default = pg_svc.strategy_spec_summary_page(
                    visible_tenants=visible, page_size=20
                )
            pg_bounded_default_ms = ((time.perf_counter() - t_wall0) / 3.0) * 1000.0
            assert pg_store.full_summary_rows_extracted == 20

            t_wall0 = time.perf_counter()
            for _ in range(3):
                pg_legacy_persona = project_strategy_spec_summary_page(
                    pg_store.list_strategy_spec_summary_rows(
                        visible_tenants=visible, artifact_type="strategy_spec"
                    ),
                    persona_id="persona-perf-target",
                    page_size=20,
                )
            pg_legacy_persona_ms = ((time.perf_counter() - t_wall0) / 3.0) * 1000.0

            t_wall0 = time.perf_counter()
            for _ in range(3):
                pg_bounded_persona = pg_svc.strategy_spec_summary_page(
                    visible_tenants=visible,
                    persona_id="persona-perf-target",
                    page_size=20,
                )
            pg_bounded_persona_ms = ((time.perf_counter() - t_wall0) / 3.0) * 1000.0
            assert pg_store.full_summary_rows_extracted == 1

            assert pg_bounded_default == pg_legacy_default
            assert pg_bounded_persona == pg_legacy_persona

            pg_default_drop = (1.0 - (pg_bounded_default_ms / pg_legacy_default_ms)) * 100.0
            pg_persona_drop = (1.0 - (pg_bounded_persona_ms / pg_legacy_persona_ms)) * 100.0
            assert pg_default_drop >= 70.0, (
                f"Expected >= 70% Postgres+Registry drop for default page, got {pg_default_drop:.1f}% "
                f"(legacy={pg_legacy_default_ms:.2f}ms, bounded={pg_bounded_default_ms:.2f}ms)"
            )
            assert pg_persona_drop >= 70.0, (
                f"Expected >= 70% Postgres+Registry drop for persona call, got {pg_persona_drop:.1f}% "
                f"(legacy={pg_legacy_persona_ms:.2f}ms, bounded={pg_bounded_persona_ms:.2f}ms)"
            )
        finally:
            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {entries_table}")
                    cur.execute(f"DROP TABLE IF EXISTS {receipts_table}")
                conn.commit()


def test_strategy_summary_page_preserves_error_statuses_for_invalid_tokens_conflicting_filters_and_owner_failures(
    strict_client,
):
    """AC5: Owner failures, invalid page tokens, and conflicting state filters
    keep today's status and error, and validation failures short-circuit before
    any store scan or full summary extraction.
    """
    from .storage import get_store

    token_a = _jwt(subject="err-contract-a", tenant="tenant-a")
    store = get_store()
    store.create(
        RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id="strat-err-1",
            version="1.0.0",
            artifact_state=ArtifactState.APPROVED,
            lineage=Lineage(source_run_ids=["run-err"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum="sha256:strat-err-1",
            metadata={"strategy_spec": _valid_spec("strat-err-1")},
        ),
        "reg-err-1",
        actor={"tenant": "tenant-a"},
    )

    # 1. Invalid non-integer page_token -> 400 and 0 full summary extractions
    store.full_summary_rows_extracted = 0
    resp_bad_token = strict_client.get(
        "/api/registry/strategy-specs?view=strategy_summary_page&page_token=not-an-int",
        headers=_bearer(token_a),
    )
    assert resp_bad_token.status_code == 400
    assert "Invalid page_token" in resp_bad_token.json()["detail"]
    assert store.full_summary_rows_extracted == 0

    # 2. Negative page_token -> 400 and 0 full summary extractions
    resp_neg_token = strict_client.get(
        "/api/registry/strategy-specs?view=strategy_summary_page&page_token=-5",
        headers=_bearer(token_a),
    )
    assert resp_neg_token.status_code == 400
    assert "Invalid page_token" in resp_neg_token.json()["detail"]
    assert store.full_summary_rows_extracted == 0

    # 3. Conflicting state and exclude_state -> 400 and 0 full summary extractions
    resp_conflict = strict_client.get(
        "/api/registry/strategy-specs?view=strategy_summary_page&state=approved&exclude_state=draft",
        headers=_bearer(token_a),
    )
    assert resp_conflict.status_code == 400
    assert "state and exclude_state cannot be combined" in resp_conflict.json()["detail"]
    assert store.full_summary_rows_extracted == 0

    # 4. Corrupt owner entry in store -> ValueError at service layer and 503 via HTTP route
    store._entries["reg-corrupt-entry"] = RegistryEntry(
        registry_id="reg-corrupt-entry",
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id="",
        version="",
        artifact_state=ArtifactState.DRAFT,
        lineage=Lineage(source_run_ids=["run-corrupt"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum="sha256:corrupt",
        metadata={},
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:00:00Z",
        owner_tenant="tenant-a",
    )
    resp_owner_err = strict_client.get(
        "/api/registry/strategy-specs?view=strategy_summary_page&page_size=20",
        headers=_bearer(token_a),
    )
    assert resp_owner_err.status_code == 503
    assert "Invalid strategy registry entry" in resp_owner_err.json()["detail"]
    assert store.full_summary_rows_extracted == 0
