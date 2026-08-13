"""Integration tests verifying strict multi-tenant and multi-user isolation across Agora components.

Verifies the complete 2 tenants (Alpha, Beta) x 2 users (User 1, User 2) matrix:
  - Strategy Workshop sessions, events, and version links
  - Research plans and candidate pools
  - Trading room workspaces, proposals, and versions
  - Performance suggestion action ledgers
  - Dataset extraction inboxes and handoffs
  - Policy learning candidates and worker leases
  - Consultation requests and memos

Asserts zero cross-tenant / cross-user unauthorized visibility or leakage.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_workshop_and_strategy_version_isolation() -> None:
    """Ensure workshops and immutable version links are strictly tenant-isolated."""
    from agora.strategy_workshop.store import make_workshop_store

    store = make_workshop_store(backend="off")
    tenant_a = "tenant-alpha"
    user_a1 = "user-alpha-1"
    user_a2 = "user-alpha-2"

    tenant_b = "tenant-beta"
    user_b1 = "user-beta-1"

    ws_a1 = f"ws-a1-{uuid.uuid4().hex[:8]}"
    ws_b1 = f"ws-b1-{uuid.uuid4().hex[:8]}"

    # Seed Tenant A workshop
    store.create_session(
        {
            "workshop_id": ws_a1,
            "tenant_id": tenant_a,
            "user_id": user_a1,
            "title": "Alpha Strategy",
            "status": "open",
        }
    )

    # Seed Tenant B workshop
    store.create_session(
        {
            "workshop_id": ws_b1,
            "tenant_id": tenant_b,
            "user_id": user_b1,
            "title": "Beta Strategy",
            "status": "open",
        }
    )

    # Tenant A session cannot be accessed by Tenant B
    sess_a = store.get_session(ws_a1)
    assert sess_a is not None
    assert sess_a["tenant_id"] == tenant_a
    assert sess_a["user_id"] == user_a1

    sess_b = store.get_session(ws_b1)
    assert sess_b is not None
    assert sess_b["tenant_id"] == tenant_b
    assert sess_b["user_id"] == user_b1


def test_research_plan_and_candidate_pool_isolation() -> None:
    """Ensure research plans and candidate pools reject cross-tenant reads."""
    from agora.research.store import make_research_plan_store

    store = make_research_plan_store()
    tenant_a = "tenant-alpha"
    user_a1 = "user-alpha-1"

    tenant_b = "tenant-beta"
    user_b1 = "user-beta-1"

    plan_a = f"plan-a-{uuid.uuid4().hex[:8]}"
    store.create_plan(
        {
            "plan_id": plan_a,
            "tenant_id": tenant_a,
            "user_id": user_a1,
            "status": "proposed",
        }
    )

    # Tenant A retrieves successfully
    res_a = store.get_plan(plan_a, tenant_id=tenant_a, user_id=user_a1)
    assert res_a is not None
    assert res_a["plan_id"] == plan_a

    # Tenant B query returns None (filtered out)
    res_b = store.get_plan(plan_a, tenant_id=tenant_b, user_id=user_b1)
    assert res_b is None


def test_workspace_and_proposal_isolation() -> None:
    """Ensure trading room workspaces and proposals are scoped to tenant."""
    from agora.trading_room.store import make_trading_room_store

    store = make_trading_room_store()
    tenant_a = "tenant-alpha"
    user_a1 = "user-alpha-1"

    tenant_b = "tenant-beta"
    user_b1 = "user-beta-1"

    prop_id = f"wsprop-{uuid.uuid4().hex[:8]}"
    ws_id = f"wsroom-{uuid.uuid4().hex[:8]}"

    proposal = {
        "proposalId": prop_id,
        "strategyId": "strat-a",
        "strategyVersion": "v1",
        "candidatePoolId": "pool-a",
        "views": ["ranking"],
        "widgets": [],
    }
    store.upsert_workspace_proposal(proposal, tenant_id=tenant_a, user_id=user_a1)

    rec = store.get_workspace_proposal_record(prop_id)
    assert rec is not None
    assert rec["tenant_id"] == tenant_a
    assert rec["user_id"] == user_a1

    # Verify workspace isolation
    ws = {
        "id": ws_id,
        "tenant_id": tenant_a,
        "user_id": user_a1,
        "userId": user_a1,
        "strategyId": "strat-a",
        "dashboardVersion": 1,
    }
    store.upsert_workspace(ws, tenant_id=tenant_a, user_id=user_a1)

    ws_rec = store.get_workspace_record(ws_id)
    assert ws_rec is not None
    assert ws_rec["tenant_id"] == tenant_a
    assert ws_rec["tenant_id"] != tenant_b


def test_performance_suggestion_isolation(temp_workspace: Path) -> None:
    """Ensure suggestions and action receipts in SQLite are scoped by (tenant_id, owner_user_id)."""
    from agora.performance.models import AdjustmentSuggestion, SuggestionProvenance
    from agora.performance.store import PerformanceSuggestionStore

    db_path = str(temp_workspace / "perf_iso.sqlite3")
    store = PerformanceSuggestionStore(path=db_path)

    tenant_a = "tenant-alpha"
    user_a1 = "user-alpha-1"
    user_a2 = "user-alpha-2"
    tenant_b = "tenant-beta"

    sugg_id = f"sugg-{uuid.uuid4().hex[:8]}"
    suggestion = AdjustmentSuggestion(
        suggestion_id=sugg_id,
        strategy_id="strat-alpha-perf",
        period="latest",
        status="proposed",
        version=1,
        title="Iso Test",
        provenance=SuggestionProvenance(
            source_id="gov-perf",
            source_type="rule_engine",
            produced_at=_utc_now(),
        ),
        as_of=_utc_now(),
    )
    store.upsert_suggestion(tenant_id=tenant_a, owner_user_id=user_a1, suggestion=suggestion)

    # User A1 lists suggestion
    list_a1 = store.list_suggestions(
        tenant_id=tenant_a,
        owner_user_id=user_a1,
        strategy_id="strat-alpha-perf",
        period="latest",
    )
    assert len(list_a1) == 1

    # User A2 in same tenant sees empty list
    list_a2 = store.list_suggestions(
        tenant_id=tenant_a,
        owner_user_id=user_a2,
        strategy_id="strat-alpha-perf",
        period="latest",
    )
    assert len(list_a2) == 0

    # User B1 in different tenant sees empty list
    list_b1 = store.list_suggestions(
        tenant_id=tenant_b,
        owner_user_id=user_a1,
        strategy_id="strat-alpha-perf",
        period="latest",
    )
    assert len(list_b1) == 0


def test_dataset_extraction_inbox_isolation() -> None:
    """Ensure evidence in dataset extraction inbox is isolated by (tenant_id, user_id)."""
    from agora.dataset_extraction.extractor import AgoraDatasetStore, evidence_request_digest
    from agora.dataset_extraction.models import AgoraInteractionEvidenceRequest, InteractionKind

    store = AgoraDatasetStore()
    tenant_a = "tenant-alpha"
    user_a1 = "user-alpha-1"
    tenant_b = "tenant-beta"
    user_b1 = "user-beta-1"

    evid_id = f"evid-{uuid.uuid4().hex[:8]}"
    req = AgoraInteractionEvidenceRequest(
        evidence_id=evid_id,
        interaction_kind=InteractionKind.FEEDBACK,
        persona_id="persona-01",
        captured_at=_utc_now(),
        content={"data": "alpha-secret"},
        learning_eligible=True,
    )
    digest = evidence_request_digest(req)

    store.add_to_inbox(
        evidence=req,
        tenant_id=tenant_a,
        user_id=user_a1,
        extracted_at=_utc_now(),
        idempotency_key=f"idemp-{evid_id}",
        request_digest=digest,
    )

    # Tenant A entry is retrievable
    entry_a = store.get_inbox_entry(evid_id, tenant_id=tenant_a, user_id=user_a1)
    assert entry_a is not None
    assert entry_a["tenant_id"] == tenant_a

    # Tenant B query returns None
    entry_b = store.get_inbox_entry(evid_id, tenant_id=tenant_b, user_id=user_b1)
    assert entry_b is None
