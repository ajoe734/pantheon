from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.governance.router import create_governance_router


OPERATOR_TOKEN = "Bearer op-2:operator"


class _EmptyReadStore:
    """A read store that implements none of the governance read hooks.

    ``GovernanceService`` falls back to empty collections and an
    ``unavailable`` dataset source for every method the store does not
    implement, so this exercises the fail-closed path of the real
    production builder.
    """


class _SeededReadStore:
    def __init__(
        self,
        *,
        requests: List[Dict[str, Any]],
        committees: List[Dict[str, Any]],
        memos: List[Dict[str, Any]],
    ) -> None:
        self._requests = requests
        self._committees = committees
        self._memos = memos

    def dataset_source(self, dataset: str) -> str:
        return "ok"

    def list_consult_requests(
        self,
        *,
        statuses: Optional[List[str]],
        target_type: Optional[str],
        consultation_type: Optional[str],
    ) -> List[Dict[str, Any]]:
        return list(self._requests)

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]],
        consensus_states: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        return list(self._committees)

    def list_consult_memos(self, *, statuses: Optional[List[str]]) -> List[Dict[str, Any]]:
        return list(self._memos)


def _client(read_store: Any) -> TestClient:
    app = FastAPI()
    app.include_router(create_governance_router(get_read_store=lambda: read_store))
    return TestClient(app)


def test_pkt015_consultation_workbench_degrades_to_empty_without_a_read_store() -> None:
    client = _client(_EmptyReadStore())

    response = client.get(
        "/api/v1/workbench/consultation",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["data"]["id"] == "consultation-workbench"
    assert payload["data"]["requests"] == []
    assert payload["data"]["committees"] == []
    assert payload["data"]["memos"] == []
    assert payload["data"]["summary"] == {
        "request_count": 0,
        "committee_count": 0,
        "memo_count": 0,
    }
    assert "snapshot_at" in payload["meta"]


def test_pkt015_consultation_workbench_aggregates_requests_committees_and_memos() -> None:
    read_store = _SeededReadStore(
        requests=[
            {"request_id": "cr-20260422-001", "status": "open"},
            {"request_id": "cr-20260422-002", "status": "closed"},
        ],
        committees=[
            {
                "committee_id": "cmt-20260422-001",
                "quorum_state": "met",
                "consensus_state": "sponsor_required",
            }
        ],
        memos=[
            {
                "memo_id": "memo-rt-20260422-001",
                "memo_type": "red_team",
                "status": "published",
                "linked_request_id": "cr-20260422-001",
            }
        ],
    )
    client = _client(read_store)

    response = client.get(
        "/api/v1/workbench/consultation",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    data = payload["data"]
    assert data["id"] == "consultation-workbench"
    assert [r["request_id"] for r in data["requests"]] == [
        "cr-20260422-001",
        "cr-20260422-002",
    ]
    assert [c["committee_id"] for c in data["committees"]] == ["cmt-20260422-001"]
    assert data["memos"][0]["memo_id"] == "memo-rt-20260422-001"
    assert data["memos"][0]["memo_type"] == "red_team"
    assert data["memos"][0]["status"] == "published"
    assert data["memos"][0]["linked_request_id"] == "cr-20260422-001"
    assert data["memos"][0]["route_href"] == "/consultation/memos/memo-rt-20260422-001"
    assert data["summary"] == {
        "request_count": 2,
        "committee_count": 1,
        "memo_count": 1,
    }
