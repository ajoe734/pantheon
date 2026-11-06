"""Strategy list default view (`exclude_state=draft`) and `meta.state_counts` tests.

Covers `BFF-STRATEGY-LIST-DEFAULT-VIEW-20261106`:
1. `GET /bff/strategies` with `exclude_state=draft` returns only non-draft strategies
   with an exact `page_info.total` and stable paging; sending `state` and `exclude_state`
   together is a validation error (400 `VALIDATION_FAILED`).
2. Without `exclude_state`, response `items` and `total` are unchanged so existing
   callers behave as today.
3. `meta.state_counts` gives the count for each normalized state value used by the
   DTO `state` field for the caller tenant from one bounded owner read; a test at
   5000 real-shape rows asserts the owner request count (1) and that counts plus
   retired reconcile with the rows; when that read fails, `state_counts` is `null`
   and never zeros while the list still returns.
4. `state=retired` returns the retired strategies although the default list keeps
   excluding them, and `state=review` returns candidate strategies.
5. A strategy whose stored lifecycle value is not a known state stays visible with
   `exclude_state=draft` and is not hidden through the normalization fallback to `draft`.
6. Tenant isolation holds for the list and for `state_counts`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.auth import policy as auth_policy
from services.control_plane.bff.command_adapters import base as adapter_base
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.research_knowledge_source import (
    DefaultResearchKnowledgeSourcePort,
)
from services.control_plane.bff.strategies import service as strategies_service_mod
from services.control_plane.bff.strategies.router import create_strategies_router
from services.control_plane.bff.strategies.routes.common import (
    default_page_slice,
    default_read_surface_meta,
)
from services.registry.models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    StorageBackend,
    StorageRef,
)
from services.registry.split_api import RegistryService
from services.registry.storage import RegistryStore

TENANT_A_HEADERS = {
    "Authorization": "Bearer op-strategy-view:operator,viewer:tenant-dev",
    "X-Tenant-Id": "tenant-dev",
}
TENANT_B_HEADERS = {
    "Authorization": "Bearer op-strategy-view-b:operator,viewer:tenant-other",
    "X-Tenant-Id": "tenant-other",
}

_STRATEGY_BFF_LIFECYCLE_MAP = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "paused": "paused",
    "retired": "retired",
    "archived": "retired",
    "rejected": "retired",
    "superseded": "retired",
    "paper": "paper_running",
    "paper_running": "paper_running",
    "canary": "canary_running",
    "canary_running": "canary_running",
    "canary_authorized_not_started": "canary_authorized_not_started",
    "live": "live_running",
    "live_running": "live_running",
    "needs_human_approval": "needs_human_approval",
    "rollback_required": "rollback_required",
    "stopped": "stopped",
    "failed": "failed",
    "provisioning": "provisioning",
    "provisioning_failed": "failed",
}


def _normalize_lifecycle_state(value: Any) -> str:
    raw = str(value or "draft").strip().lower()
    return _STRATEGY_BFF_LIFECYCLE_MAP.get(raw, "draft")


def _normalize_risk_level(value: Any) -> str:
    raw = str(value or "medium").strip().lower()
    return raw if raw in {"low", "medium", "high", "critical"} else "medium"


@pytest.fixture(autouse=True)
def _permissive_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "1")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")


def _make_registry_entry(
    *,
    registry_id: str,
    strategy_id: str,
    version: str,
    artifact_state: ArtifactState,
    owner_tenant: str,
    title: str,
    updated_at: str,
    persona_ids: Optional[List[str]] = None,
    spec_lifecycle: Optional[str] = None,
    alpha: Optional[str] = None,
    bulk_padding: str = "",
) -> RegistryEntry:
    spec_lifecycle_val = spec_lifecycle if spec_lifecycle is not None else artifact_state.value
    market_scope: Dict[str, Any] = {
        "market": "TW_EQUITY",
        "symbols": ["2330.TW", "2317.TW"],
        "asset_classes": ["EQUITY"],
        "venues": ["TWSE"],
        "frequency": "1d",
    }
    if alpha is not None:
        market_scope["alpha"] = alpha
    inner_spec: Dict[str, Any] = {
        "strategy_id": strategy_id,
        "spec_version": version,
        "lifecycle_state": spec_lifecycle_val,
        "title": title,
        "hypothesis": f"Hypothesis for {strategy_id} {bulk_padding}",
        "objective": f"Objective for {strategy_id}",
        "market_scope": market_scope,
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "WEIGHT",
            "rebalance_cadence": "daily",
            "execution_mode_hint": "paper",
            "capital_pool_id": f"pool-{strategy_id}",
        },
        "evaluation_plan": {
            "metrics": ["sharpe_ratio", "max_drawdown"],
            "paper_gate": "sharpe_ratio >= 1.1",
        },
        "governance": {
            "approval_required": True,
            "risk_level": "medium",
            "owner": "research-team",
        },
        "provenance": {
            "source_kind": "research_run_output",
            "created_at": updated_at,
            "created_by": "op-seed",
        },
        "persona_ids": list(persona_ids or []),
        "metadata": {
            "tenant_id": owner_tenant,
            "market": "TW",
            "execution_context": "paper",
        },
    }
    return RegistryEntry(
        registry_id=registry_id,
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id=strategy_id,
        version=version,
        artifact_state=artifact_state,
        lineage=Lineage(source_run_ids=[f"run-{strategy_id}"]),
        storage_ref=StorageRef(
            backend=StorageBackend.INLINE,
            path="$.entry.metadata.strategy_spec",
        ),
        checksum=f"sha256:{registry_id}",
        evaluation_summary={"risk_level": "medium"},
        metadata={
            "tenant_id": owner_tenant,
            "title": title,
            "persona_ids": list(persona_ids or []),
            "capital_pool_id": f"pool-{strategy_id}",
            "strategy_spec": inner_spec,
        },
        created_at=updated_at,
        updated_at=updated_at,
        owner_tenant=owner_tenant,
    )


def _build_5000_real_shape_registry_entries() -> List[RegistryEntry]:
    """Build 5000 real-shape strategy_spec RegistryEntry rows for tenant-dev plus
    foreign tenant rows:
    - 133 multi-version families (266 rows): v1.0.0 draft -> v1.1.0 approved (100)
      or paper_running (20) or deployed (13)
    - 80 candidate families (80 rows) -> DTO state 'review'
    - 15 retired families (15 rows) -> DTO state 'retired'
    - 4 unknown-lifecycle families (4 rows, stored lifecycle 'experimental_stage')
      -> DTO state 'draft' via fallback, but NOT excluded by exclude_state=draft
    - 4635 single-version draft families (4635 rows) -> DTO state 'draft'
    Total tenant-dev rows = 266 + 80 + 15 + 4 + 4635 = 5000 rows across 4867 families:
      - approved: 100
      - paper_running: 20
      - deployed: 13
      - review: 80
      - draft: 4635 (true draft) + 4 (unknown fallback) = 4639
      - retired: 15
      - default list total (non-retired): 4852
      - exclude_state=draft total (non-retired, non-true-draft): 100 + 20 + 13 + 80 + 4 = 217
    Plus 25 rows for tenant-other to verify tenant isolation.
    """
    entries: List[RegistryEntry] = []
    bulk = "B" * 256

    # 1. 133 2-version families (266 rows)
    for idx in range(1, 134):
        sid = f"strat-5k-promoted-{idx:04d}"
        entries.append(
            _make_registry_entry(
                registry_id=f"reg-{sid}-v1",
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.DRAFT,
                owner_tenant="tenant-dev",
                title=f"Promoted Strategy {idx:04d} v1",
                updated_at="2026-11-01T09:00:00Z",
                persona_ids=[f"persona-{idx:04d}"],
                bulk_padding=bulk,
            )
        )
        if idx <= 100:
            spec_lc = "approved"
        elif idx <= 120:
            spec_lc = "paper_running"
        else:
            spec_lc = "deployed"
        entries.append(
            _make_registry_entry(
                registry_id=f"reg-{sid}-v2",
                strategy_id=sid,
                version="1.1.0",
                artifact_state=ArtifactState.APPROVED,
                owner_tenant="tenant-dev",
                title=f"Promoted Strategy {idx:04d}",
                updated_at=f"2026-11-05T15:{idx % 60:02d}:00Z",
                persona_ids=[f"persona-{idx:04d}"],
                spec_lifecycle=spec_lc,
                alpha="tw-momentum",
                bulk_padding=bulk,
            )
        )

    # 2. 80 candidate families (80 rows)
    for idx in range(1, 81):
        sid = f"strat-5k-candidate-{idx:04d}"
        entries.append(
            _make_registry_entry(
                registry_id=f"reg-{sid}-v1",
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.CANDIDATE,
                owner_tenant="tenant-dev",
                title=f"Candidate Strategy {idx:04d}",
                updated_at=f"2026-11-05T14:{idx % 60:02d}:00Z",
                persona_ids=[f"persona-cand-{idx:04d}"],
                bulk_padding=bulk,
            )
        )

    # 3. 15 retired families (15 rows)
    for idx in range(1, 16):
        sid = f"strat-5k-retired-{idx:04d}"
        entries.append(
            _make_registry_entry(
                registry_id=f"reg-{sid}-v1",
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.RETIRED,
                owner_tenant="tenant-dev",
                title=f"Retired Strategy {idx:04d}",
                updated_at=f"2026-11-02T10:{idx % 60:02d}:00Z",
                bulk_padding=bulk,
            )
        )

    # 4. 4 unknown stored lifecycle families (4 rows)
    for idx in range(1, 5):
        sid = f"strat-5k-unknown-{idx:04d}"
        entries.append(
            _make_registry_entry(
                registry_id=f"reg-{sid}-v1",
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.DRAFT,
                owner_tenant="tenant-dev",
                title=f"Experimental Unknown Strategy {idx:04d}",
                updated_at=f"2026-11-05T16:0{idx}:00Z",
                spec_lifecycle="experimental_stage",
                bulk_padding=bulk,
            )
        )

    # 5. 4635 true draft families (4635 rows) -> total 5000 rows for tenant-dev
    for idx in range(1, 4636):
        sid = f"strat-5k-draft-{idx:04d}"
        entries.append(
            _make_registry_entry(
                registry_id=f"reg-{sid}-v1",
                strategy_id=sid,
                version="1.0.0",
                artifact_state=ArtifactState.DRAFT,
                owner_tenant="tenant-dev",
                title=f"Draft Strategy {idx:04d}",
                updated_at="2026-11-03T08:00:00Z",
                bulk_padding=bulk,
            )
        )

    tenant_dev_rows = [e for e in entries if e.owner_tenant == "tenant-dev"]
    assert len(tenant_dev_rows) == 5000

    # 6. 25 foreign tenant rows (tenant-other)
    for idx in range(1, 26):
        sid = f"strat-other-tenant-{idx:04d}"
        st = ArtifactState.APPROVED if idx <= 15 else (ArtifactState.DRAFT if idx <= 22 else ArtifactState.RETIRED)
        entries.append(
            _make_registry_entry(
                registry_id=f"reg-{sid}-v1",
                strategy_id=sid,
                version="1.0.0",
                artifact_state=st,
                owner_tenant="tenant-other",
                title=f"Other Tenant Strategy {idx:04d}",
                updated_at="2026-11-05T12:00:00Z",
            )
        )

    return entries


def _build_registry_backed_app(
    monkeypatch: pytest.MonkeyPatch,
    entries: List[RegistryEntry],
    owner_http_calls: List[Tuple[str, int]],
    *,
    mutate_owner_payload: Optional[Any] = None,
) -> Tuple[FastAPI, RegistryService]:
    reg_store = RegistryStore()
    for entry in entries:
        reg_store._entries[entry.registry_id] = entry
    reg_service = RegistryService(store=reg_store)

    def _owner_http_stub(url: str, **kwargs: Any) -> Any:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        tenant_id = str(kwargs.get("tenant_id") or "tenant-dev")
        visible = frozenset({tenant_id, "__builtin__"})
        if parsed.path == "/api/registry/strategy-specs" and qs.get("view") == ["strategy_summary_page"]:
            try:
                payload = reg_service.strategy_spec_summary_page(
                    visible_tenants=visible,
                    artifact_type="strategy_spec",
                    state=qs["state"][0] if "state" in qs else None,
                    exclude_state=qs["exclude_state"][0] if "exclude_state" in qs else None,
                    persona_id=qs["persona_id"][0] if "persona_id" in qs else None,
                    page_token=qs["page_token"][0] if "page_token" in qs else None,
                    page_size=int(qs["page_size"][0]) if "page_size" in qs else 20,
                )
            except ValueError as exc:
                raise urllib.error.HTTPError(
                    url, 400, str(exc), hdrs=None, fp=None
                ) from exc
            if callable(mutate_owner_payload):
                payload = mutate_owner_payload(payload)
            owner_http_calls.append((f"{parsed.path}?{parsed.query}", len(payload.get("items") or [])))
            return payload
        raise AssertionError(f"Unexpected owner HTTP call: {url}")

    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.local")
    monkeypatch.setattr(adapter_base, "http_request_json", _owner_http_stub)

    rks_port = DefaultResearchKnowledgeSourcePort()
    read_ports = create_read_surface_ports(research_knowledge_source=rks_port)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=lambda: strategies_service_mod.list_strategy_summaries(read_ports),
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-06T04:00:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=_normalize_lifecycle_state,
            normalize_risk_level=_normalize_risk_level,
        )
    )
    app.add_middleware(OwnerReadContextMiddleware)
    return app, reg_service


def test_strategy_list_5000_rows_default_exclude_draft_state_counts_retired_and_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _build_5000_real_shape_registry_entries()
    owner_http_calls: List[Tuple[str, int]] = []
    app, _ = _build_registry_backed_app(monkeypatch, entries, owner_http_calls)
    client = TestClient(app)

    # 1. Without exclude_state, default view returns all 4852 non-retired strategies
    #    in 1 bounded owner request, and meta.state_counts reconciles with all 4867 families.
    owner_http_calls.clear()
    resp_default = client.get("/bff/strategies?page_size=20", headers=TENANT_A_HEADERS)
    assert resp_default.status_code == 200, resp_default.text
    default_body = resp_default.json()
    assert len(owner_http_calls) == 1
    assert owner_http_calls[0] == (
        "/api/registry/strategy-specs?view=strategy_summary_page&page_size=20",
        20,
    )
    assert default_body["page_info"]["total"] == 4852
    assert default_body["meta"]["total"] == 4852
    assert default_body["page_info"]["next_page_token"] == "20"
    assert len(default_body["items"]) == 20

    state_counts = default_body["meta"]["state_counts"]
    assert state_counts == {
        "draft": 4639,  # 4635 true drafts + 4 unknown-lifecycle fallback to draft
        "review": 80,
        "approved": 100,
        "paper_running": 20,
        "canary_running": 0,
        "live_running": 0,
        "deployed": 13,
        "paused": 0,
        "retired": 15,
    }
    # Reconcile state_counts with non-retired default total and total families (4867 across 5000 rows)
    non_retired_count_sum = sum(v for k, v in state_counts.items() if k != "retired")
    assert non_retired_count_sum == default_body["page_info"]["total"] == 4852
    assert non_retired_count_sum + state_counts["retired"] == 4867

    # 2. exclude_state=draft returns only non-draft strategies (plus unknown lifecycle
    #    strategies that must not be hidden), with exact total=217 and stable paging.
    owner_http_calls.clear()
    resp_no_draft_p1 = client.get(
        "/bff/strategies?exclude_state=draft&page_size=100",
        headers=TENANT_A_HEADERS,
    )
    assert resp_no_draft_p1.status_code == 200, resp_no_draft_p1.text
    p1_body = resp_no_draft_p1.json()
    assert len(owner_http_calls) == 1
    assert owner_http_calls[0] == (
        "/api/registry/strategy-specs?view=strategy_summary_page&page_size=100&exclude_state=draft",
        100,
    )
    assert p1_body["page_info"]["total"] == 217
    assert p1_body["meta"]["total"] == 217
    assert p1_body["page_info"]["next_page_token"] == "100"
    assert p1_body["meta"]["state_counts"] == state_counts

    # Page through all pages of exclude_state=draft and verify stability & zero true drafts
    collected: List[Dict[str, Any]] = list(p1_body["items"])
    token = p1_body["page_info"]["next_page_token"]
    while token is not None:
        resp_page = client.get(
            f"/bff/strategies?exclude_state=draft&page_size=100&page_token={token}",
            headers=TENANT_A_HEADERS,
        )
        assert resp_page.status_code == 200, resp_page.text
        page_body = resp_page.json()
        assert page_body["page_info"]["total"] == 217
        collected.extend(page_body["items"])
        token = page_body["page_info"]["next_page_token"]

    assert len(collected) == 217
    collected_ids = [item["id"] for item in collected]
    assert len(set(collected_ids)) == 217
    assert not any(sid.startswith("strat-5k-draft-") for sid in collected_ids)
    assert not any(sid.startswith("strat-5k-retired-") for sid in collected_ids)

    # 3. Unknown stored lifecycle value stays visible under exclude_state=draft
    #    even though DTO state normalizes to "draft" as display fallback.
    unknown_items = [item for item in collected if item["id"].startswith("strat-5k-unknown-")]
    assert len(unknown_items) == 4
    for item in unknown_items:
        assert item["state"] == "draft"
        assert item["lifecycleStatus"] == "experimental_stage"

    # All other 213 items have non-draft DTO states
    known_non_draft_items = [item for item in collected if not item["id"].startswith("strat-5k-unknown-")]
    assert len(known_non_draft_items) == 213
    assert all(item["state"] != "draft" for item in known_non_draft_items)

    # 4. state=retired returns the 15 retired strategies
    owner_http_calls.clear()
    resp_retired = client.get(
        "/bff/strategies?state=retired&page_size=50",
        headers=TENANT_A_HEADERS,
    )
    assert resp_retired.status_code == 200, resp_retired.text
    retired_body = resp_retired.json()
    assert len(owner_http_calls) == 1
    assert retired_body["page_info"]["total"] == 15
    assert len(retired_body["items"]) == 15
    assert all(item["state"] == "retired" for item in retired_body["items"])
    assert all(item["id"].startswith("strat-5k-retired-") for item in retired_body["items"])

    # 5. state=review returns the 80 candidate strategies
    resp_review = client.get(
        "/bff/strategies?state=review&page_size=100",
        headers=TENANT_A_HEADERS,
    )
    assert resp_review.status_code == 200, resp_review.text
    review_body = resp_review.json()
    assert review_body["page_info"]["total"] == 80
    assert len(review_body["items"]) == 80
    assert all(item["state"] == "review" for item in review_body["items"])
    assert all(item["id"].startswith("strat-5k-candidate-") for item in review_body["items"])


def test_strategy_list_conflicting_state_and_exclude_state_is_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _build_5000_real_shape_registry_entries()[:10]
    owner_http_calls: List[Tuple[str, int]] = []
    app, _ = _build_registry_backed_app(monkeypatch, entries, owner_http_calls)
    client = TestClient(app)

    resp = client.get(
        "/bff/strategies?state=approved&exclude_state=draft",
        headers=TENANT_A_HEADERS,
    )
    assert resp.status_code == 400, resp.text
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_FAILED"
    reason_text = str(
        err.get("reason")
        or (err.get("details") or {}).get("reason")
        or (err.get("details") or {}).get("precondition_failed")
        or err.get("message")
        or ""
    )
    assert "exclude_state" in reason_text
    # Rejected at BFF validation boundary before any owner HTTP call
    assert owner_http_calls == []


def test_strategy_list_state_counts_failure_returns_null_never_zeros_while_list_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _build_5000_real_shape_registry_entries()[:30]
    owner_http_calls: List[Tuple[str, int]] = []

    # Case 1: Owner payload has state_counts=None (count computation failed on owner)
    app_null, _ = _build_registry_backed_app(
        monkeypatch,
        entries,
        owner_http_calls,
        mutate_owner_payload=lambda p: {**p, "state_counts": None},
    )
    client_null = TestClient(app_null)
    resp_null = client_null.get("/bff/strategies?exclude_state=draft&page_size=20", headers=TENANT_A_HEADERS)
    assert resp_null.status_code == 200, resp_null.text
    body_null = resp_null.json()
    assert body_null["page_info"]["total"] == 15
    assert len(body_null["items"]) == 15
    assert body_null["meta"]["state_counts"] is None

    # Case 2: Owner payload has malformed/corrupt state_counts
    app_bad, _ = _build_registry_backed_app(
        monkeypatch,
        entries,
        owner_http_calls,
        mutate_owner_payload=lambda p: {**p, "state_counts": {"draft": "corrupt"}},
    )
    client_bad = TestClient(app_bad)
    resp_bad = client_bad.get("/bff/strategies?page_size=20", headers=TENANT_A_HEADERS)
    assert resp_bad.status_code == 200, resp_bad.text
    body_bad = resp_bad.json()
    assert body_bad["page_info"]["total"] == 15
    assert body_bad["meta"]["state_counts"] is None

    # Case 3: Custom get_strategy_state_counts raises an exception while list succeeds
    app_exc, _ = _build_registry_backed_app(monkeypatch, entries, owner_http_calls)

    def _boom_counts() -> Dict[str, int]:
        raise RuntimeError("state_counts read failed")

    rks_port = DefaultResearchKnowledgeSourcePort()
    read_ports = create_read_surface_ports(research_knowledge_source=rks_port)
    monkeypatch.setattr(read_ports, "get_strategy_state_counts", _boom_counts, raising=False)
    app_exc_custom = FastAPI()
    register_error_handlers(app_exc_custom)
    app_exc_custom.include_router(
        create_strategies_router(
            read_surface=read_ports,
            list_strategy_summaries=lambda: strategies_service_mod.list_strategy_summaries(read_ports),
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
            utc_now=lambda: "2026-11-06T04:00:00Z",
            page_slice=default_page_slice,
            read_surface_meta=default_read_surface_meta,
            normalize_lifecycle_state=_normalize_lifecycle_state,
            normalize_risk_level=_normalize_risk_level,
        )
    )
    app_exc_custom.add_middleware(OwnerReadContextMiddleware)
    client_exc = TestClient(app_exc_custom)
    resp_exc = client_exc.get("/bff/strategies?page_size=20", headers=TENANT_A_HEADERS)
    assert resp_exc.status_code == 200, resp_exc.text
    body_exc = resp_exc.json()
    assert body_exc["page_info"]["total"] == 15
    assert body_exc["meta"]["state_counts"] is None


def test_strategy_list_tenant_isolation_for_items_and_state_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _build_5000_real_shape_registry_entries()
    owner_http_calls: List[Tuple[str, int]] = []
    app, _ = _build_registry_backed_app(monkeypatch, entries, owner_http_calls)
    client = TestClient(app)

    # Tenant B sees only its 22 non-retired strategies (15 approved + 7 draft) and 3 retired in state_counts
    resp_b_default = client.get("/bff/strategies?page_size=50", headers=TENANT_B_HEADERS)
    assert resp_b_default.status_code == 200, resp_b_default.text
    b_default = resp_b_default.json()
    assert b_default["page_info"]["total"] == 22
    assert all(item["id"].startswith("strat-other-tenant-") for item in b_default["items"])
    assert b_default["meta"]["state_counts"] == {
        "draft": 7,
        "review": 0,
        "approved": 15,
        "paper_running": 0,
        "canary_running": 0,
        "live_running": 0,
        "deployed": 0,
        "paused": 0,
        "retired": 3,
    }

    resp_b_no_draft = client.get(
        "/bff/strategies?exclude_state=draft&page_size=50",
        headers=TENANT_B_HEADERS,
    )
    assert resp_b_no_draft.status_code == 200, resp_b_no_draft.text
    b_no_draft = resp_b_no_draft.json()
    assert b_no_draft["page_info"]["total"] == 15
    assert all(item["state"] == "approved" for item in b_no_draft["items"])
    assert all(item["id"].startswith("strat-other-tenant-") for item in b_no_draft["items"])

    resp_b_retired = client.get(
        "/bff/strategies?state=retired&page_size=50",
        headers=TENANT_B_HEADERS,
    )
    assert resp_b_retired.status_code == 200, resp_b_retired.text
    b_retired = resp_b_retired.json()
    assert b_retired["page_info"]["total"] == 3
    assert all(item["state"] == "retired" for item in b_retired["items"])
    assert all(item["id"].startswith("strat-other-tenant-") for item in b_retired["items"])


# --- BFF-STRATEGY-LIST-RISK-TRUTH-20261106 ---
# List risk comes from evaluation_summary.risk_level. Real hosted row shape
# (reg-strategy-spec-persona-298e138c136c9840b0e4): top-level/metadata/execution risk empty,
# governance has only risk_profile, evaluation_summary.risk_level == "low".
RISK_TRUTH_SID = "strategy-persona-298e138c136c9840b0e4"
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
        _persona_entry("reg-strategy-spec-persona-298e138c136c9840b0e4", RISK_TRUTH_SID, ArtifactState.APPROVED, "low"),
        _persona_entry("reg-strategy-spec-persona-nolevel", "strategy-persona-nolevel", ArtifactState.APPROVED, None),
    ]
    calls: List[Tuple[str, int]] = []
    app, _ = _build_registry_backed_app(monkeypatch, entries, calls)
    client = TestClient(app)

    listing = client.get("/bff/strategies?exclude_state=draft&page_size=200", headers=TENANT_A_HEADERS)
    assert listing.status_code == 200, listing.text
    rows = {r["id"]: r for r in listing.json()["items"]}
    assert rows[RISK_TRUTH_SID]["risk"] == "low"
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
        _persona_entry("reg-strategy-spec-persona-298e138c136c9840b0e4", RISK_TRUTH_SID, ArtifactState.APPROVED, "low"),
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
    assert rows[RISK_TRUTH_SID]["risk"] == "low"
    assert rows["strategy-persona-nolevel"]["risk"] == "medium"
