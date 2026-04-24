from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore, _default_read_data


OPERATOR_AUTH = "Bearer test-operator:operator"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"
PUBLISHED_MEMO_ID = "memo-rt-20260419-081"
DRAFT_MEMO_ID = "memo-rt-20260420-002"


@contextmanager
def _seeded_client(*, service_backed_memo_store: bool = False):
    tracked_env = {
        "PANTHEON_BFF_CONSULT_MEMO_STORE": os.environ.get("PANTHEON_BFF_CONSULT_MEMO_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        memo_store_path: Path | None = None
        if service_backed_memo_store:
            memo_store_path = Path(td) / "consult_memos.json"
            memo_store_path.write_text(
                json.dumps(
                    _default_read_data()["consult_memos"],
                    indent=2,
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            os.environ["PANTHEON_BFF_CONSULT_MEMO_STORE"] = str(memo_store_path)
        else:
            os.environ.pop("PANTHEON_BFF_CONSULT_MEMO_STORE", None)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client, memo_store_path
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _load_memo_store(memo_store_path: Path) -> dict[str, dict[str, object]]:
    return json.loads(memo_store_path.read_text(encoding="utf-8"))


def _write_memo_store(memo_store_path: Path, payload: dict[str, dict[str, object]]) -> None:
    memo_store_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def test_cw04_list_returns_required_envelope_with_degraded_snapshot_surface() -> None:
    with _seeded_client() as (client, _memo_store_path):
        response = client.get(
            "/api/v1/consult/memos?status=published&page_size=1",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["page_info"] == {
            "next_page_token": None,
            "page_size": 1,
            "total": 1,
        }
        assert payload["items"] == [
            {
                "object_ref": {
                    "type": "ConsultMemo",
                    "id": PUBLISHED_MEMO_ID,
                },
                "memo_id": PUBLISHED_MEMO_ID,
                "memo_type": "red_team",
                "status": "published",
                "linked_request_id": "cr-20260419-014",
                "recommendation_count": 3,
                "published_at": "2026-04-19T17:22:00Z",
                "created_at": "2026-04-19T17:15:00Z",
                "route_href": f"/consultation/memos/{PUBLISHED_MEMO_ID}",
            }
        ]
        assert payload["meta"]["surfaces"]["redteam_memo"]["state"] == "degraded"
        assert payload["meta"]["staleness"]["status"] == "stale"


def test_cw04_detail_returns_backend_owned_shape_and_governance_gate_for_reviewer() -> None:
    with _seeded_client(service_backed_memo_store=True) as (client, memo_store_path):
        assert memo_store_path is not None

        response = client.get(
            f"/api/v1/consult/memos/{PUBLISHED_MEMO_ID}",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["object_ref"] == {
            "type": "ConsultMemo",
            "id": PUBLISHED_MEMO_ID,
        }
        assert payload["memo_type"] == "red_team"
        assert payload["status"] == "published"
        assert payload["lifecycle_state"] == "published"
        assert payload["author_ref"] == "p-risk-analyst"
        assert payload["linked_request_id"] == "cr-20260419-014"
        assert payload["linked_session_id"] == "cs-20260419-081"
        assert payload["session_to_memo_mapping"] == {
            "mapping_id": "map-20260419-081",
            "source_session_id": "cs-20260419-081",
            "transcript_id": "tr-cs-20260419-081",
            "transcript_version": "v1",
            "memo_id": PUBLISHED_MEMO_ID,
            "memo_type": "red_team",
            "created_by": {
                "actor_type": "persona",
                "actor_id": "p-risk-analyst",
            },
            "evidence_refs": [
                "telemetry-vol-spike-20260419",
                "dp-20260419-014",
            ],
            "mapping_status": "active",
            "created_at": "2026-04-19T17:18:00Z",
        }
        assert payload["recommendations"][0].startswith("Raise the ATR-based circuit-breaker threshold")
        assert payload["evidence_refs"][0] == {
            "id": "telemetry-vol-spike-20260419",
            "evidence_type": "telemetry",
            "artifact_ref": "artifact-042",
            "description": "Volatility spike - 2026-04-19",
            "link": "/telemetry/events/telemetry-vol-spike-20260419",
        }
        assert payload["allowedActions"] == {
            "canInitiateGovernanceReview": True,
        }
        assert payload["meta"]["surfaces"]["redteam_memo"]["state"] == "ok"
        assert payload["meta"]["staleness"]["status"] == "fresh"


def test_cw04_detail_hides_governance_handoff_for_operator_without_review_authority() -> None:
    with _seeded_client(service_backed_memo_store=True) as (client, memo_store_path):
        assert memo_store_path is not None

        response = client.get(
            f"/api/v1/consult/memos/{PUBLISHED_MEMO_ID}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["allowedActions"] == {
            "canInitiateGovernanceReview": False,
        }


def test_cw04_draft_memo_never_allows_governance_handoff() -> None:
    with _seeded_client(service_backed_memo_store=True) as (client, memo_store_path):
        assert memo_store_path is not None

        response = client.get(
            f"/api/v1/consult/memos/{DRAFT_MEMO_ID}",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["status"] == "draft"
        assert payload["lifecycle_state"] == "draft"
        assert payload["allowedActions"] == {
            "canInitiateGovernanceReview": False,
        }


def test_cw04_detail_hides_governance_handoff_when_active_review_exists() -> None:
    with _seeded_client(service_backed_memo_store=True) as (client, memo_store_path):
        assert memo_store_path is not None
        memos = _load_memo_store(memo_store_path)
        memos[PUBLISHED_MEMO_ID]["active_governance_review_id"] = "gov-review-rt-001"
        _write_memo_store(memo_store_path, memos)

        response = client.get(
            f"/api/v1/consult/memos/{PUBLISHED_MEMO_ID}",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["allowedActions"] == {
            "canInitiateGovernanceReview": False,
        }
        assert payload["meta"]["surfaces"]["redteam_memo"]["state"] == "ok"


def test_cw04_detail_keeps_last_known_content_when_surface_degraded() -> None:
    with _seeded_client(service_backed_memo_store=True) as (client, memo_store_path):
        assert memo_store_path is not None
        memos = _load_memo_store(memo_store_path)
        memos[PUBLISHED_MEMO_ID]["surface_state"] = "degraded"
        _write_memo_store(memo_store_path, memos)

        response = client.get(
            f"/api/v1/consult/memos/{PUBLISHED_MEMO_ID}",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["author_ref"] == "p-risk-analyst"
        assert payload["linked_request_id"] == "cr-20260419-014"
        assert payload["linked_session_id"] == "cs-20260419-081"
        assert payload["session_to_memo_mapping"] == {
            "mapping_id": "map-20260419-081",
            "source_session_id": "cs-20260419-081",
            "transcript_id": "tr-cs-20260419-081",
            "transcript_version": "v1",
            "memo_id": PUBLISHED_MEMO_ID,
            "memo_type": "red_team",
            "created_by": {
                "actor_type": "persona",
                "actor_id": "p-risk-analyst",
            },
            "evidence_refs": [
                "telemetry-vol-spike-20260419",
                "dp-20260419-014",
            ],
            "mapping_status": "active",
            "created_at": "2026-04-19T17:18:00Z",
        }
        assert payload["summary"] is not None
        assert payload["recommendations"]
        assert payload["evidence_refs"] == [
            {
                "id": "telemetry-vol-spike-20260419",
                "evidence_type": "telemetry",
                "artifact_ref": "artifact-042",
                "description": "Volatility spike - 2026-04-19",
                "link": "/telemetry/events/telemetry-vol-spike-20260419",
            },
            {
                "id": "dp-20260419-014",
                "evidence_type": "deployment_plan",
                "artifact_ref": "plan-F-042",
                "description": "Deployment plan plan-F-042",
                "link": "/deployments/plans/plan-F-042",
            },
        ]
        assert payload["allowedActions"] == {
            "canInitiateGovernanceReview": False,
        }
        assert payload["meta"]["surfaces"]["redteam_memo"]["state"] == "degraded"
        assert payload["meta"]["staleness"]["status"] == "stale"
        assert payload["published_at"] == "2026-04-19T17:22:00Z"
        assert payload["created_at"] == "2026-04-19T17:15:00Z"



def test_cw04_detail_hides_memo_content_when_surface_unavailable() -> None:
    with _seeded_client(service_backed_memo_store=True) as (client, memo_store_path):
        assert memo_store_path is not None
        memos = _load_memo_store(memo_store_path)
        memos[PUBLISHED_MEMO_ID]["surface_state"] = "unavailable"
        _write_memo_store(memo_store_path, memos)

        response = client.get(
            f"/api/v1/consult/memos/{PUBLISHED_MEMO_ID}",
            headers={"Authorization": REVIEWER_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["summary"] is None
        assert payload["recommendations"] == []
        assert payload["evidence_refs"] == []
        assert payload["allowedActions"] == {
            "canInitiateGovernanceReview": False,
        }
        assert payload["meta"]["surfaces"]["redteam_memo"]["state"] == "unavailable"
        assert payload["meta"]["staleness"]["status"] == "stale"
