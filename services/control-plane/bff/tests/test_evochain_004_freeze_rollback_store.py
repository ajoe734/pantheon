"""EVOCHAIN-004 BFF service-client and surface-status contracts."""
from __future__ import annotations

import os
import sys
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main

OPERATOR_HEADERS = {"Authorization": "Bearer op-evochain-004:operator,reviewer"}
GOVERNANCE_URL = "http://governance:8082"
EVOLUTION_URL = "http://evolution:8093"
POSTMORTEMS_URL = "http://postmortems:8091"


def _configure_service_urls(monkeypatch) -> None:
    values = {
        # The legacy alias intentionally points at evolution in deployed
        # service-family configuration. New governance reads must ignore it.
        "PANTHEON_GOVERNANCE_API_URL": EVOLUTION_URL,
        "PANTHEON_GOVERNANCE_SERVICE_URL": "",
        "PANTHEON_GOVERNANCE_APPROVAL_API_URL": GOVERNANCE_URL,
        "PANTHEON_PROMOTION_API_URL": "",
        "PANTHEON_EVOLUTION_API_URL": EVOLUTION_URL,
        "PANTHEON_POSTMORTEMS_API_URL": POSTMORTEMS_URL,
        "PANTHEON_BFF_FREEZE_ORDER_STORE": "",
        "PANTHEON_BFF_ROLLBACK_STORE": "",
        "PANTHEON_GOVERNANCE_DATA_DIR": "",
        "GOVERNANCE_DATA_DIR": "",
        "BFF_READ_SURFACE_STATE": "fresh",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _fake_service_get(
    freeze_payload: Any,
    rollback_payload: Any,
    *,
    freeze_available: bool = True,
    rollback_available: bool = True,
):
    responses = {
        (GOVERNANCE_URL, "/api/governance/approvals"): (True, []),
        (GOVERNANCE_URL, "/api/governance/freeze-orders"): (freeze_available, freeze_payload),
        (GOVERNANCE_URL, "/api/governance/rollbacks"): (rollback_available, rollback_payload),
        (EVOLUTION_URL, "/api/evolution/proposals"): (True, []),
        (POSTMORTEMS_URL, "/api/postmortems"): (True, []),
    }

    def fake_get(base_url: str, path: str, *, headers=None):
        return responses.get((base_url, path), (False, None))

    return fake_get


class FreezeRollbackTestStore:
    def __init__(self, *, allow_local_snapshot_fallback: bool, fake_get):
        self.allow_fallback = allow_local_snapshot_fallback
        self.fake_get = fake_get

    def _call_service(self, dataset: str):
        gov_url = os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL") or os.getenv("PANTHEON_GOVERNANCE_API_URL")
        if dataset == "freeze_orders":
            available, payload = self.fake_get(gov_url, "/api/governance/freeze-orders")
            return available, payload
        elif dataset == "all_rollbacks":
            available, payload = self.fake_get(gov_url, "/api/governance/rollbacks")
            return available, payload
        return False, None

    def dataset_source(self, dataset: str) -> str:
        if dataset in ("freeze_orders", "all_rollbacks"):
            available, payload = self._call_service(dataset)
            if available:
                if isinstance(payload, list):
                    return "service_client"
                return "missing"
            if self.allow_fallback:
                return "local_snapshot"
            return "missing"
        return "service_client"

    def list_freeze_orders(self, status: str | None = None, scope: str | None = None) -> list[dict]:
        available, payload = self._call_service("freeze_orders")
        if available and isinstance(payload, list):
            orders = [dict(x) for x in payload]
            if status:
                orders = [o for o in orders if o.get("status") == status]
            if scope:
                orders = [o for o in orders if o.get("scope") == scope]
            orders.sort(key=lambda x: str(x.get("issued_at") or ""), reverse=True)
            return orders
        if self.allow_fallback and not available:
            return [{
                "freeze_order_id": "freeze-fallback",
                "status": "active",
                "scope": "persona",
                "issued_at": "2026-07-13T09:00:00Z",
            }]
        return []

    def list_all_rollbacks(
        self,
        runtime_id: str | None = None,
        action_type: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        available, payload = self._call_service("all_rollbacks")
        if available and isinstance(payload, list):
            rbs = [dict(x) for x in payload]
            if runtime_id:
                rbs = [r for r in rbs if r.get("runtime_id") == runtime_id]
            if action_type:
                rbs = [r for r in rbs if r.get("action_type") == action_type]
            if status:
                rbs = [r for r in rbs if r.get("status") == status]
            rbs.sort(key=lambda x: str(x.get("requested_at") or ""), reverse=True)
            return rbs
        if self.allow_fallback and not available:
            return [{
                "rollback_id": "rollback-fallback",
                "runtime_id": "runtime-fallback",
                "action_type": "replace",
                "status": "completed",
                "requested_at": "2026-07-13T09:00:00Z",
            }]
        return []

    def list_evolution_decisions(self, **kw) -> list[dict]:
        return []

    def list_postmortems(self, **kw) -> list[dict]:
        return []

    def list_personas(self, **kw) -> list[dict]:
        return []


def _install_store(tmp_path, monkeypatch, *, allow_fallback: bool, fake_get) -> FreezeRollbackTestStore:
    _configure_service_urls(monkeypatch)
    store = FreezeRollbackTestStore(
        allow_local_snapshot_fallback=allow_fallback,
        fake_get=fake_get,
    )
    monkeypatch.setattr(bff_main, "read_store", store)
    return store


def test_healthy_empty_service_is_ok_and_does_not_mix_local_seed(tmp_path, monkeypatch) -> None:
    store = _install_store(
        tmp_path,
        monkeypatch,
        allow_fallback=True,
        fake_get=_fake_service_get([], []),
    )

    assert store.list_freeze_orders() == []
    assert store.list_all_rollbacks() == []
    assert store.dataset_source("freeze_orders") == "service_client"
    assert store.dataset_source("all_rollbacks") == "service_client"

    response = TestClient(bff_main.app).get(
        "/bff/management/evolution-journal",
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["items"] == []
    surfaces = body["meta"]["surfaces"]
    assert surfaces["freeze_orders"]["status"] == "ok"
    assert surfaces["freeze_orders"]["source"] == "service_client"
    assert surfaces["rollbacks"]["status"] == "ok"
    assert surfaces["rollbacks"]["source"] == "service_client"
    assert surfaces["mutation_review"]["status"] == "ok"
    assert surfaces["management_evolution_journal"]["status"] == "ok"


def test_explicit_governance_url_wins_over_legacy_evolution_alias(tmp_path, monkeypatch) -> None:
    calls = []
    delegate = _fake_service_get([], [])

    def recording_get(base_url: str, path: str, *, headers=None):
        calls.append((base_url, path))
        return delegate(base_url, path, headers=headers)

    store = _install_store(
        tmp_path,
        monkeypatch,
        allow_fallback=False,
        fake_get=recording_get,
    )

    assert store.list_freeze_orders() == []
    assert store.list_all_rollbacks() == []
    assert (GOVERNANCE_URL, "/api/governance/freeze-orders") in calls
    assert (GOVERNANCE_URL, "/api/governance/rollbacks") in calls
    assert (EVOLUTION_URL, "/api/governance/freeze-orders") not in calls
    assert (EVOLUTION_URL, "/api/governance/rollbacks") not in calls


def test_populated_service_records_are_filtered_sorted_and_projected(tmp_path, monkeypatch) -> None:
    freeze_records = [
        {
            "freeze_order_id": "freeze-older",
            "status": "released",
            "scope": "persona",
            "issued_at": "2026-07-13T08:00:00Z",
        },
        {
            "freeze_order_id": "freeze-active",
            "status": "active",
            "scope": "persona",
            "issued_at": "2026-07-13T09:00:00Z",
        },
    ]
    rollback_records = [
        {
            "rollback_id": "rollback-older",
            "runtime_id": "runtime-alpha",
            "action_type": "replace",
            "status": "completed",
            "requested_at": "2026-07-13T08:30:00Z",
        },
        {
            "rollback_id": "rollback-latest",
            "runtime_id": "runtime-beta",
            "action_type": "pause_then_replace",
            "status": "accepted",
            "requested_at": "2026-07-13T09:30:00Z",
        },
    ]
    store = _install_store(
        tmp_path,
        monkeypatch,
        allow_fallback=False,
        fake_get=_fake_service_get(freeze_records, rollback_records),
    )

    freezes = store.list_freeze_orders(scope="persona")
    assert [record["freeze_order_id"] for record in freezes] == ["freeze-active", "freeze-older"]
    assert [record["freeze_order_id"] for record in store.list_freeze_orders(status="active")] == [
        "freeze-active"
    ]
    rollbacks = store.list_all_rollbacks()
    assert [record["rollback_id"] for record in rollbacks] == ["rollback-latest", "rollback-older"]
    assert [
        record["rollback_id"]
        for record in store.list_all_rollbacks(
            runtime_id="runtime-beta",
            action_type="pause_then_replace",
        )
    ] == ["rollback-latest"]

    response = TestClient(bff_main.app).get(
        "/bff/management/evolution-journal",
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["summary"]["freeze_order_count"] == 2
    assert body["data"]["summary"]["rollback_count"] == 2
    assert {item["entry_type"] for item in body["data"]["items"]} == {"freeze_order", "rollback"}


def test_invalid_or_not_found_list_payload_is_not_a_healthy_empty_store(tmp_path, monkeypatch) -> None:
    store = _install_store(
        tmp_path,
        monkeypatch,
        allow_fallback=False,
        fake_get=_fake_service_get(None, {"items": []}),
    )

    assert store.list_freeze_orders() == []
    assert store.list_all_rollbacks() == []
    assert store.dataset_source("freeze_orders") == "missing"
    assert store.dataset_source("all_rollbacks") == "missing"

    response = TestClient(bff_main.app).get(
        "/bff/management/evolution-journal",
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200, response.text
    surfaces = response.json()["meta"]["surfaces"]
    assert surfaces["freeze_orders"]["status"] == "unavailable"
    assert surfaces["rollbacks"]["status"] == "unavailable"


def test_local_snapshot_is_used_only_after_service_failure(tmp_path, monkeypatch) -> None:
    store = _install_store(
        tmp_path,
        monkeypatch,
        allow_fallback=True,
        fake_get=_fake_service_get(None, None, freeze_available=False, rollback_available=False),
    )

    assert store.list_freeze_orders()
    assert store.list_all_rollbacks()
    assert store.dataset_source("freeze_orders") == "local_snapshot"
    assert store.dataset_source("all_rollbacks") == "local_snapshot"
