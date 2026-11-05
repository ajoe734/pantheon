"""Contract and integration tests for ARTIFACT-CATALOG-REGISTRY-20261104.

Verifies:
1. Registry `GET /api/registry/strategy-artifacts` flat execution_bundle catalog projection,
   tenant visibility, paging, total, and exclusion of `strategy_spec` and full `metadata`.
2. `/bff/artifacts` merges Registry execution bundles with Research run outputs newest-first,
   carrying `id`, `name`, `kind`, `version`, `state`, `risk`, `hash`, `owner`, `updatedAt`,
   and `source_kind` from the respective owner, keeping `version`, `state`, and `risk` as
   `None` (`null`) on Research run outputs.
3. 251 real-shape execution bundles (~5.9 KB each) + 2 research outputs = 253 total,
   `strategy_spec` excluded, and paging reaches every row.
4. `/bff/artifacts/{id}` resolves both Registry IDs and Research artifact IDs, returns 404
   for unknown IDs, and never exposes another tenant's Registry entry.
5. Single-owner failure returns the surviving source with `meta.surfaces.artifact_list`
   status `degraded` naming the failed source and `page_info.total = None`
   (`total_complete = False`); dual-owner failure returns `unavailable`.
6. `/api/v1/research/artifacts`, detail, and compare remain Research-only and unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.research.router import create_research_router
from services.registry import service as registry_service_module
from services.registry.models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    StorageBackend,
    StorageRef,
)
from services.registry.storage import get_store, reset_store
from services.runtime_auth_inbound import encode_jwt_hs256


TENANT_VISIBLE = "tenant-alpha"
TENANT_OTHER = "tenant-bravo"
_JWT_SECRET = "artifact-catalog-registry-test-secret"

CATALOG_ROW_KEYS = {
    "id",
    "name",
    "kind",
    "version",
    "state",
    "risk",
    "hash",
    "owner",
    "updatedAt",
    "source_kind",
}


def _bff_error(status_code: int, code: ErrorCode, message: str, reason: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code.value, "message": message, "reason": reason, **extra},
    )


def _jwt(*, subject: str = "bff-service", tenant: str = TENANT_VISIBLE, roles=("operator",)) -> str:
    return encode_jwt_hs256(
        {
            "sub": subject,
            "tenant": tenant,
            "roles": list(roles),
            "exp": time.time() + 3600,
        },
        secret=_JWT_SECRET,
    )


def _build_real_shape_bundle_entry(
    index: int,
    *,
    artifact_state: ArtifactState,
    tenant_id: str,
    created_at: datetime,
) -> RegistryEntry:
    # Build a ~5.9 KB metadata payload matching hosted execution_bundle row shape.
    padding_signals = [
        {
            "signal_id": f"sig-{index:03d}-{slot:02d}",
            "factor_expr": f"Rank(Corr(close, volume, {slot + 5})) - Mean(ret_5d, {slot + 10})",
            "weight": round(0.02 * ((slot % 10) + 1), 4),
            "universe": "tw_equities_top200",
            "lookback_bars": 60 + slot,
        }
        for slot in range(24)
    ]
    strategy_id = f"strat-persona-bundle-{index:03d}"
    version = f"1.0.{index}"
    created_iso = created_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return RegistryEntry(
        registry_id=f"reg-bundle-{index:03d}",
        artifact_type=ArtifactType.EXECUTION_BUNDLE,
        strategy_id=strategy_id,
        version=version,
        artifact_state=artifact_state,
        lineage=Lineage(
            source_run_ids=[f"run-bundle-{index:03d}"],
            source_strategy_spec_id=f"spec-{index:03d}",
        ),
        storage_ref=StorageRef(
            backend=StorageBackend.INLINE,
            path="$.entry.metadata.strategy_artifact",
        ),
        checksum="a" * 60 + f"{index:04d}",
        producer_run_id=f"run-bundle-{index:03d}",
        evaluation_summary={
            "admission": "persona_paper_bootstrap",
            "risk_level": "low",
            "capital_scale_pct": 0.0,
            "execution_context": "paper",
            "sharpe_ratio": 1.42,
            "max_drawdown": 0.068,
            "tenant_id": tenant_id,
        },
        approver="gov-approver-alpha" if artifact_state != ArtifactState.CANDIDATE else None,
        approved_at=created_iso if artifact_state != ArtifactState.CANDIDATE else None,
        approval_decision_id=f"dec-{index:03d}" if artifact_state != ArtifactState.CANDIDATE else None,
        created_at=created_iso,
        updated_at=created_iso,
        owner_tenant=tenant_id,
        metadata={
            "tenant_id": tenant_id,
            "strategy_artifact": {
                "schema_version": "strategy_artifact.v1",
                "tenant_id": tenant_id,
                "strategy_id": strategy_id,
                "version": version,
                "signals": padding_signals,
                "execution_policy": {
                    "order_type": "limit",
                    "rebalance_cadence": "daily",
                    "max_turnover_pct": 0.15,
                    "notes": "x" * 620,
                },
            },
        },
    )


def _build_strategy_spec_entry(index: int, *, tenant_id: str, created_at: datetime) -> RegistryEntry:
    created_iso = created_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return RegistryEntry(
        registry_id=f"reg-spec-{index:03d}",
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id=f"strat-spec-{index:03d}",
        version="1.0.0",
        artifact_state=ArtifactState.APPROVED,
        lineage=Lineage(source_run_ids=[f"run-spec-{index:03d}"]),
        storage_ref=StorageRef(
            backend=StorageBackend.OBJECT_STORE,
            path=f"s3://pantheon-specs/{tenant_id}/strat-spec-{index:03d}/1.0.0/spec.json",
        ),
        checksum="c" * 60 + f"{index:04d}",
        producer_run_id=f"run-spec-{index:03d}",
        evaluation_summary={"sharpe_ratio": 1.1},
        created_at=created_iso,
        updated_at=created_iso,
        owner_tenant=tenant_id,
        metadata={
            "tenant_id": tenant_id,
            "strategy_spec": {
                "strategy_id": f"strat-spec-{index:03d}",
                "tenant_id": tenant_id,
                "lifecycle_state": "approved",
            },
        },
    )


def _build_research_run_outputs(base_time: datetime) -> List[Dict[str, Any]]:
    # Two real-shape research run outputs (one newest overall, one in the middle of the bundles)
    t_newest = (base_time + timedelta(minutes=300)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    t_mid = (base_time + timedelta(minutes=120, seconds=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return [
        {
            "artifact_id": "res-run-output-001",
            "title": "Qlib Alpha158 Factor Evaluation Output",
            "artifact_type": "factor_eval_report",
            "created_at": t_newest,
            "created_by": "research-worker-qlib",
            "run_id": "orch-run-001",
            "task_id": "task-qlib-001",
            "stage_id": "stage-eval",
            "checksum": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            "storage_ref": "gs://pantheon-research-outputs/orch-run-001/report.json",
            "artifact_family": "evaluation",
            "stage_type": "backtest",
            "tenant_id": TENANT_VISIBLE,
            "run": {
                "receipt_id": "rcpt-001",
                "artifact_digest": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                "status": "completed",
                "completed_at": t_newest,
                "provenance": "qlib-runner",
            },
        },
        {
            "artifact_id": "res-run-output-002",
            "title": "Walk-Forward IC Decay Diagnostic",
            "artifact_type": "diagnostic_bundle",
            "created_at": t_mid,
            "created_by": "research-worker-diag",
            "run_id": "orch-run-002",
            "task_id": "task-diag-002",
            "stage_id": "stage-diag",
            "checksum": "sha256:4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
            "storage_ref": "gs://pantheon-research-outputs/orch-run-002/diag.json",
            "artifact_family": "diagnostics",
            "stage_type": "analysis",
            "tenant_id": TENANT_VISIBLE,
            "run": {
                "receipt_id": "rcpt-002",
                "artifact_digest": "sha256:4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
                "status": "completed",
                "completed_at": t_mid,
                "provenance": "diag-runner",
            },
        },
    ]


class _FakeResearchWriteOwner:
    def __init__(self, rows: List[Dict[str, Any]], *, fail_on_list: bool = False) -> None:
        self._rows = list(rows)
        self.fail_on_list = fail_on_list

    def list_research_artifacts(self) -> List[Dict[str, Any]]:
        if self.fail_on_list:
            raise RuntimeError("Research orchestrator artifact endpoint timed out")
        return list(self._rows)

    def get_research_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        if self.fail_on_list:
            raise RuntimeError("Research orchestrator artifact detail endpoint timed out")
        for row in self._rows:
            if row.get("artifact_id") == artifact_id:
                return dict(row)
        return None


@pytest.fixture()
def seeded_catalog_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    reset_store()
    store = get_store()
    store._entries.clear()

    base_time = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

    # Seed 251 execution_bundle rows for TENANT_VISIBLE: 249 approved, 1 candidate, 1 retired.
    bundle_sizes: List[int] = []
    for idx in range(1, 252):
        if idx == 250:
            state = ArtifactState.CANDIDATE
        elif idx == 251:
            state = ArtifactState.RETIRED
        else:
            state = ArtifactState.APPROVED
        entry = _build_real_shape_bundle_entry(
            idx,
            artifact_state=state,
            tenant_id=TENANT_VISIBLE,
            created_at=base_time + timedelta(minutes=idx),
        )
        bundle_sizes.append(len(json.dumps(entry.to_dict()).encode("utf-8")))
        store._entries[entry.registry_id] = entry

    # Seed 3 strategy_spec rows for TENANT_VISIBLE (must be excluded from artifact catalog).
    for idx in range(1, 4):
        spec_entry = _build_strategy_spec_entry(
            idx,
            tenant_id=TENANT_VISIBLE,
            created_at=base_time + timedelta(minutes=400 + idx),
        )
        store._entries[spec_entry.registry_id] = spec_entry

    # Seed 1 cross-tenant execution_bundle row for TENANT_OTHER (must never be exposed to TENANT_VISIBLE).
    other_tenant_entry = _build_real_shape_bundle_entry(
        999,
        artifact_state=ArtifactState.APPROVED,
        tenant_id=TENANT_OTHER,
        created_at=base_time + timedelta(minutes=500),
    )
    other_tenant_entry.registry_id = "reg-other-tenant-secret"
    store._entries[other_tenant_entry.registry_id] = other_tenant_entry

    registry_client = TestClient(registry_service_module.app, raise_server_exceptions=False)
    # Remove any startup-seeded builtins so the fixture tests exact 251 + 2 = 253 population
    for k in list(store._entries.keys()):
        if not k.startswith(("reg-bundle-", "reg-spec-", "reg-other-tenant-")):
            store._entries.pop(k, None)

    real_urlopen = urllib.request.urlopen
    state_flags = {"fail_registry": False}

    class _FakeHTTPResponse:
        def __init__(self, status: int, body: bytes) -> None:
            self.status = status
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> "_FakeHTTPResponse":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    def _bridged_urlopen(req: Any, timeout: Optional[float] = None, *args: Any, **kwargs: Any) -> Any:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.startswith("http://registry.test"):
            if state_flags["fail_registry"]:
                raise urllib.error.URLError("Registry service connection refused")
            path = url[len("http://registry.test") :] or "/"
            headers = dict(req.header_items()) if hasattr(req, "header_items") else {}
            resp = registry_client.get(path, headers=headers)
            if resp.status_code >= 400:
                raise urllib.error.HTTPError(
                    url,
                    resp.status_code,
                    resp.text,
                    hdrs=None,  # type: ignore[arg-type]
                    fp=io.BytesIO(resp.content),
                )
            return _FakeHTTPResponse(resp.status_code, resp.content)
        return real_urlopen(req, timeout=timeout, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", _bridged_urlopen)

    research_outputs = _build_research_run_outputs(base_time)
    research_owner = _FakeResearchWriteOwner(research_outputs)

    port = DefaultResearchKnowledgeSourcePort(
        registry_service_url="http://registry.test",
        research_write_owner=research_owner,
    )

    bff_app = FastAPI()
    bff_app.include_router(
        create_research_router(
            get_read_store=lambda: port,
            extract_identity=lambda _auth: SimpleNamespace(operator_id="op-alpha", tenant_id=TENANT_VISIBLE),
            require_read_role=lambda _identity: None,
            require_operator_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-11-05T00:00:00Z",
        )
    )
    bff_client = TestClient(bff_app)

    tok_auth = authorization.set(f"Bearer {_jwt(tenant=TENANT_VISIBLE)}")
    tok_tenant = selected_tenant.set(TENANT_VISIBLE)
    try:
        yield {
            "registry_client": registry_client,
            "bff_client": bff_client,
            "research_owner": research_owner,
            "state_flags": state_flags,
            "avg_bundle_bytes": sum(bundle_sizes) / len(bundle_sizes),
            "port": port,
        }
    finally:
        authorization.reset(tok_auth)
        selected_tenant.reset(tok_tenant)
        reset_store()


def test_artifact_catalog_merges_251_registry_bundles_and_2_research_outputs_newest_first_and_pages_all(
    seeded_catalog_env: Dict[str, Any],
) -> None:
    bff_client: TestClient = seeded_catalog_env["bff_client"]
    avg_bundle_bytes: float = seeded_catalog_env["avg_bundle_bytes"]
    assert 5500 <= avg_bundle_bytes <= 6500, f"Expected ~5.9 KB average bundle size, got {avg_bundle_bytes:.0f} B"

    # Measure first-page latency and body size
    t0 = time.perf_counter()
    first_resp = bff_client.get("/bff/artifacts", params={"page_size": 20})
    first_page_latency_ms = (time.perf_counter() - t0) * 1000.0
    assert first_resp.status_code == 200, first_resp.text
    first_page_bytes = len(first_resp.content)
    first_body = first_resp.json()

    assert first_body["meta"]["surfaces"]["artifact_list"]["status"] == "ok"
    assert first_body["page_info"]["total"] == 253
    assert len(first_body["items"]) == 20
    assert first_body["data"] == first_body["items"]
    assert first_page_bytes < 16384, f"First page body should stay compact (<16 KB), got {first_page_bytes} B"
    assert first_page_latency_ms < 1000.0

    # Walk all pages and collect every row
    all_rows: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    page_count = 0
    while True:
        params: Dict[str, Any] = {"page_size": 50}
        if page_token is not None:
            params["page_token"] = page_token
        resp = bff_client.get("/bff/artifacts", params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page_info"]["total"] == 253
        items = body["items"]
        assert items, "Expected non-empty page while paging"
        all_rows.extend(items)
        page_count += 1
        page_token = body["page_info"]["next_page_token"]
        if page_token is None:
            break
        assert page_count <= 10

    assert len(all_rows) == 253
    seen_ids = [row["id"] for row in all_rows]
    assert len(set(seen_ids)) == 253

    # Verify newest-first ordering across all 253 rows
    timestamps = [row["updatedAt"] for row in all_rows]
    assert timestamps == sorted(timestamps, reverse=True)
    # The newest item is res-run-output-001 (base_time + 300m)
    assert all_rows[0]["id"] == "res-run-output-001"

    # Verify every row has exact required keys and no full metadata
    for row in all_rows:
        assert set(row.keys()) == CATALOG_ROW_KEYS
        assert "metadata" not in row

    # Verify strategy_spec and other-tenant rows are excluded
    assert not any(str(r["id"]).startswith("reg-spec-") for r in all_rows)
    assert "reg-other-tenant-secret" not in seen_ids
    assert not any(r["kind"] == "strategy_spec" for r in all_rows)

    # Verify the 2 research run outputs keep null version, state, and risk
    research_rows = [r for r in all_rows if r["source_kind"] == "research_run_output"]
    assert len(research_rows) == 2
    for r_row in research_rows:
        assert r_row["version"] is None
        assert r_row["state"] is None
        assert r_row["risk"] is None
        assert r_row["hash"] is not None
        assert r_row["owner"] in {"research-worker-qlib", "research-worker-diag"}

    # Verify the 251 registry execution bundle rows carry owner values
    registry_rows = [r for r in all_rows if r["source_kind"] == "registry"]
    assert len(registry_rows) == 251
    states = [r["state"] for r in registry_rows]
    assert states.count("approved") == 249
    assert states.count("candidate") == 1
    assert states.count("retired") == 1
    for reg_row in registry_rows:
        assert reg_row["kind"] == "execution_bundle"
        assert reg_row["risk"] == "low"
        assert reg_row["version"].startswith("1.0.")
        assert len(reg_row["hash"]) == 64


def test_artifact_catalog_detail_resolves_registry_and_research_ids_and_enforces_tenant_isolation(
    seeded_catalog_env: Dict[str, Any],
) -> None:
    bff_client: TestClient = seeded_catalog_env["bff_client"]

    # 1. Resolve a visible Registry execution_bundle ID
    reg_resp = bff_client.get("/bff/artifacts/reg-bundle-001")
    assert reg_resp.status_code == 200, reg_resp.text
    reg_data = reg_resp.json()["data"]
    assert reg_data["id"] == "reg-bundle-001"
    assert reg_data["registry_id"] == "reg-bundle-001"
    assert reg_data["name"] == "strat-persona-bundle-001"
    assert reg_data["kind"] == "execution_bundle"
    assert reg_data["source_kind"] == "registry"
    assert reg_data["state"] == "approved"
    assert reg_data["risk"] == "low"
    assert reg_data["version"] == "1.0.1"
    assert reg_data["owner"] == "gov-approver-alpha"

    # 2. Resolve a Research run output ID
    res_resp = bff_client.get("/bff/artifacts/res-run-output-001")
    assert res_resp.status_code == 200, res_resp.text
    res_data = res_resp.json()["data"]
    assert res_data["id"] == "res-run-output-001"
    assert res_data["artifact_id"] == "res-run-output-001"
    assert res_data["name"] == "Qlib Alpha158 Factor Evaluation Output"
    assert res_data["kind"] == "factor_eval_report"
    assert res_data["source_kind"] == "research_run_output"
    assert res_data["version"] is None
    assert res_data["state"] is None
    assert res_data["risk"] is None

    # 3. Unknown ID returns 404
    unknown_resp = bff_client.get("/bff/artifacts/non-existent-artifact-999")
    assert unknown_resp.status_code == 404

    # 4. Another tenant's Registry entry is never exposed (returns 404)
    cross_tenant_resp = bff_client.get("/bff/artifacts/reg-other-tenant-secret")
    assert cross_tenant_resp.status_code == 404

    # 5. A strategy_spec Registry entry is not exposed as an artifact catalog detail (returns 404)
    spec_resp = bff_client.get("/bff/artifacts/reg-spec-001")
    assert spec_resp.status_code == 404


def test_artifact_catalog_degraded_when_one_owner_fails_and_unavailable_when_both_fail(
    seeded_catalog_env: Dict[str, Any],
) -> None:
    bff_client: TestClient = seeded_catalog_env["bff_client"]
    research_owner: _FakeResearchWriteOwner = seeded_catalog_env["research_owner"]
    state_flags: Dict[str, bool] = seeded_catalog_env["state_flags"]

    # Case 1: Registry fails, Research succeeds -> returns Research rows, surface degraded naming "registry", total not complete
    state_flags["fail_registry"] = True
    research_owner.fail_on_list = False
    resp_reg_down = bff_client.get("/bff/artifacts", params={"page_size": 20})
    assert resp_reg_down.status_code == 200, resp_reg_down.text
    body_reg_down = resp_reg_down.json()
    surface_reg_down = body_reg_down["meta"]["surfaces"]["artifact_list"]
    assert surface_reg_down["status"] == "degraded"
    assert surface_reg_down["failed_source"] == "registry"
    assert "registry" in surface_reg_down["failed_sources"]
    assert body_reg_down["page_info"]["total"] is None
    assert body_reg_down["page_info"]["total_complete"] is False
    assert len(body_reg_down["items"]) == 2
    assert all(r["source_kind"] == "research_run_output" for r in body_reg_down["items"])

    # Case 2: Research fails, Registry succeeds -> returns Registry rows, surface degraded naming "research", total not complete
    state_flags["fail_registry"] = False
    research_owner.fail_on_list = True
    resp_res_down = bff_client.get("/bff/artifacts", params={"page_size": 20})
    assert resp_res_down.status_code == 200, resp_res_down.text
    body_res_down = resp_res_down.json()
    surface_res_down = body_res_down["meta"]["surfaces"]["artifact_list"]
    assert surface_res_down["status"] == "degraded"
    assert surface_res_down["failed_source"] == "research"
    assert "research" in surface_res_down["failed_sources"]
    assert body_res_down["page_info"]["total"] is None
    assert body_res_down["page_info"]["total_complete"] is False
    assert len(body_res_down["items"]) == 20
    assert all(r["source_kind"] == "registry" for r in body_res_down["items"])

    # Case 3: Both fail -> surface is unavailable
    state_flags["fail_registry"] = True
    research_owner.fail_on_list = True
    resp_both_down = bff_client.get("/bff/artifacts", params={"page_size": 20})
    assert resp_both_down.status_code == 200, resp_both_down.text
    body_both_down = resp_both_down.json()
    surface_both_down = body_both_down["meta"]["surfaces"]["artifact_list"]
    assert surface_both_down["status"] == "unavailable"
    assert body_both_down["items"] == []
    assert body_both_down["page_info"]["total"] is None


def test_research_artifacts_api_v1_stays_research_only_and_compare_detail_unchanged(
    seeded_catalog_env: Dict[str, Any],
) -> None:
    bff_client: TestClient = seeded_catalog_env["bff_client"]

    # /api/v1/research/artifacts must stay Research-only (2 research outputs, 0 registry bundles)
    res_list = bff_client.get("/api/v1/research/artifacts")
    assert res_list.status_code == 200, res_list.text
    res_list_body = res_list.json()
    assert res_list_body["total_count"] == 2
    assert [a["artifact_id"] for a in res_list_body["artifacts"]] == [
        "res-run-output-001",
        "res-run-output-002",
    ]

    # /api/v1/research/artifacts/{id} does not resolve Registry IDs
    reg_on_res_route = bff_client.get("/api/v1/research/artifacts/reg-bundle-001")
    assert reg_on_res_route.status_code == 404

    # /api/v1/research/artifacts/{id} still resolves Research IDs with the RW-05 detail contract
    res_detail = bff_client.get("/api/v1/research/artifacts/res-run-output-001")
    assert res_detail.status_code == 200, res_detail.text
    res_detail_body = res_detail.json()
    assert res_detail_body["artifact_id"] == "res-run-output-001"
    assert res_detail_body["source_kind"] == "research_run_output"
    assert res_detail_body["allowedActions"] == {"canCompare": False}
