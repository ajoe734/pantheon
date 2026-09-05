from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main


OPERATOR_AUTH = "Bearer test-operator:operator"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"
PUBLISHED_MEMO_ID = "memo-rt-20260419-081"
DRAFT_MEMO_ID = "memo-rt-20260420-002"


def _default_consult_memos() -> Dict[str, Dict[str, Any]]:
    return {
        PUBLISHED_MEMO_ID: {
            "memo_id": PUBLISHED_MEMO_ID,
            "memo_type": "red_team",
            "status": "published",
            "lifecycle_state": "published",
            "author_ref": "p-risk-analyst",
            "linked_request_id": "cr-20260419-014",
            "linked_session_id": "cs-20260419-081",
            "session_to_memo_mapping": {
                "mapping_id": "map-20260419-081",
                "source_session_id": "cs-20260419-081",
                "transcript_id": "tr-cs-20260419-081",
                "transcript_version": "v1",
                "memo_id": PUBLISHED_MEMO_ID,
                "memo_type": "red_team",
                "created_by": {"actor_type": "persona", "actor_id": "p-risk-analyst"},
                "evidence_refs": ["telemetry-vol-spike-20260419", "dp-20260419-014"],
                "mapping_status": "active",
                "created_at": "2026-04-19T17:18:00Z",
            },
            "summary": (
                "Red-team memo flags volatility-guard gaps, rollback sequencing risk, "
                "and missing governance handoff proof before paper-to-live promotion."
            ),
            "recommendations": [
                "Raise the ATR-based circuit-breaker threshold before approving macro-regime deployment promotion.",
                "Add an explicit volatility guard in the rebalance path instead of relying on degraded-mode persona behavior.",
                "Run a rollback drill for position-limit breach scenarios before any live promotion decision.",
            ],
            "evidence_refs": [
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
            ],
            "published_at": "2026-04-19T17:22:00Z",
            "created_at": "2026-04-19T17:15:00Z",
            "supersedes_memo_id": None,
            "superseded_by_memo_id": None,
            "surface_state": "ok",
            "governance_target": {
                "target_type": "deployment_plan",
                "target_id": "plan-F-042",
                "deployment_plan_id": "plan-F-042",
                "artifact_id": None,
                "strategy_id": None,
            },
            "suppressed": False,
            "withdrawn": False,
            "active_governance_review_id": None,
        },
        DRAFT_MEMO_ID: {
            "memo_id": DRAFT_MEMO_ID,
            "memo_type": "red_team",
            "status": "draft",
            "lifecycle_state": "draft",
            "author_ref": "p-execution-lead",
            "linked_request_id": "cr-20260419-014",
            "linked_session_id": "cs-20260419-081",
            "session_to_memo_mapping": {
                "mapping_id": "map-20260420-002",
                "source_session_id": "cs-20260419-081",
                "transcript_id": "tr-cs-20260419-081",
                "transcript_version": "v2",
                "memo_id": DRAFT_MEMO_ID,
                "memo_type": "red_team",
                "created_by": {"actor_type": "persona", "actor_id": "p-execution-lead"},
                "evidence_refs": ["telemetry-vol-spike-20260419"],
                "mapping_status": "active",
                "created_at": "2026-04-20T08:45:00Z",
            },
            "summary": "Draft follow-up memo awaiting rollback drill evidence before publication.",
            "recommendations": [
                "Attach rollback-drill evidence before promoting this follow-up memo to published.",
            ],
            "evidence_refs": [
                {
                    "id": "telemetry-vol-spike-20260419",
                    "evidence_type": "telemetry",
                    "artifact_ref": "artifact-042",
                    "description": "Volatility spike - 2026-04-19",
                    "link": "/telemetry/events/telemetry-vol-spike-20260419",
                },
            ],
            "published_at": None,
            "created_at": "2026-04-20T08:40:00Z",
            "supersedes_memo_id": PUBLISHED_MEMO_ID,
            "superseded_by_memo_id": None,
            "surface_state": "ok",
            "governance_target": {
                "target_type": "artifact",
                "target_id": "artifact-042",
                "deployment_plan_id": None,
                "artifact_id": "artifact-042",
                "strategy_id": None,
            },
            "suppressed": False,
            "withdrawn": False,
            "active_governance_review_id": None,
        },
    }


def _default_read_data() -> Dict[str, Any]:
    """Minimal default consult-memo fixture data.

    Only the `consult_memos` sub-tree is needed by these tests (used to seed
    a service-backed memo store file, mirroring the original fixture).
    """
    return {"consult_memos": _default_consult_memos()}


def _cw04_normalize_evidence_ref(raw: Any) -> Dict[str, Any]:
    evidence_ref = raw if isinstance(raw, dict) else {}
    ref_id = str(evidence_ref.get("id") or evidence_ref.get("ref_id") or "").strip()
    link = evidence_ref.get("link") or evidence_ref.get("route_href")
    if not link and ref_id:
        link = f"/evidence/{ref_id}"
    return {
        "id": ref_id,
        "evidence_type": evidence_ref.get("evidence_type") or evidence_ref.get("type"),
        "artifact_ref": evidence_ref.get("artifact_ref"),
        "description": evidence_ref.get("description") or evidence_ref.get("display_label"),
        "link": link,
    }


def _project_summary(memo: Dict[str, Any]) -> Dict[str, Any]:
    memo_id = str(memo.get("memo_id") or "").strip()
    recommendations = list(memo.get("recommendations") or [])
    return {
        "object_ref": {"type": "ConsultMemo", "id": memo_id},
        "memo_id": memo_id,
        "memo_type": memo.get("memo_type") or "red_team",
        "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
        "linked_request_id": memo.get("linked_request_id"),
        "recommendation_count": len(recommendations),
        "published_at": memo.get("published_at"),
        "created_at": memo.get("created_at"),
        "route_href": f"/consultation/memos/{memo_id}" if memo_id else None,
    }


def _project_detail(memo: Dict[str, Any]) -> Dict[str, Any]:
    memo_id = str(memo.get("memo_id") or "").strip()
    mapping = memo.get("session_to_memo_mapping") if isinstance(memo.get("session_to_memo_mapping"), dict) else {}
    governance_target = memo.get("governance_target") if isinstance(memo.get("governance_target"), dict) else {}
    return {
        "object_ref": {"type": "ConsultMemo", "id": memo_id},
        "memo_id": memo_id,
        "memo_type": memo.get("memo_type") or "red_team",
        "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
        "lifecycle_state": memo.get("lifecycle_state") or memo.get("status") or "draft",
        "author_ref": memo.get("author_ref"),
        "linked_request_id": memo.get("linked_request_id"),
        "linked_session_id": memo.get("linked_session_id"),
        "session_to_memo_mapping": {
            "mapping_id": mapping.get("mapping_id"),
            "source_session_id": mapping.get("source_session_id"),
            "transcript_id": mapping.get("transcript_id"),
            "transcript_version": mapping.get("transcript_version"),
            "memo_id": mapping.get("memo_id") or memo_id,
            "memo_type": mapping.get("memo_type") or memo.get("memo_type") or "red_team",
            "created_by": json.loads(json.dumps(mapping.get("created_by") or {})),
            "evidence_refs": list(mapping.get("evidence_refs") or []),
            "mapping_status": mapping.get("mapping_status"),
            "created_at": mapping.get("created_at"),
        },
        "summary": memo.get("summary"),
        "recommendations": list(memo.get("recommendations") or []),
        "evidence_refs": [_cw04_normalize_evidence_ref(item) for item in (memo.get("evidence_refs") or [])],
        "published_at": memo.get("published_at"),
        "created_at": memo.get("created_at"),
        "supersedes_memo_id": memo.get("supersedes_memo_id"),
        "superseded_by_memo_id": memo.get("superseded_by_memo_id"),
        "surface_state": memo.get("surface_state") or "ok",
        "governance_target": json.loads(json.dumps(governance_target)),
        "suppressed": bool(memo.get("suppressed")),
        "withdrawn": bool(memo.get("withdrawn")),
        "active_governance_review_id": memo.get("active_governance_review_id"),
    }


class _MemoReadStore:
    """CW-04 in-memory consult-memo read double.

    Serves consult memos from an in-memory default fixture (surfaced as
    "local_snapshot", i.e. degraded/stale) unless
    PANTHEON_BFF_CONSULT_MEMO_STORE points at a JSON file, in which case that
    file is treated as the backend-owned "service_store" (ok/fresh).
    """

    def __init__(self, path: str, allow_local_snapshot_fallback: bool = True) -> None:
        self._path = path
        self._data: Dict[str, Any] = {"consult_memos": _default_consult_memos()}

    def _memo_store_env_path(self) -> Optional[str]:
        raw = os.environ.get("PANTHEON_BFF_CONSULT_MEMO_STORE", "").strip()
        return raw or None

    def _service_memo_records(self) -> Optional[Dict[str, Dict[str, Any]]]:
        path = self._memo_store_env_path()
        if not path or not Path(path).exists():
            return None
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _read_memos(self) -> Dict[str, Dict[str, Any]]:
        service_records = self._service_memo_records()
        if service_records is not None:
            return service_records
        return self._data.get("consult_memos", {})

    def dataset_source(self, dataset: str) -> str:
        if dataset != "consult_memos":
            return "missing"
        if self._service_memo_records() is not None:
            return "service_store"
        return "local_snapshot"

    def list_consult_memos(self, *, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        memos = list(self._read_memos().values())
        if statuses:
            requested = {str(value).strip().lower() for value in statuses if str(value).strip()}
            memos = [
                memo for memo in memos
                if str(memo.get("status") or memo.get("lifecycle_state") or "").strip().lower() in requested
            ]
        memos.sort(key=lambda memo: str(memo.get("published_at") or memo.get("created_at") or ""), reverse=True)
        return [_project_summary(memo) for memo in memos]

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not memo_id:
            return None
        memo = self._read_memos().get(memo_id)
        return _project_detail(memo) if memo else None


@contextmanager
def _seeded_client(*, service_backed_memo_store: bool = False):
    tracked_env = {
        "PANTHEON_BFF_CONSULT_MEMO_STORE": os.environ.get("PANTHEON_BFF_CONSULT_MEMO_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        memo_store_path: Optional[Path] = None
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
        bff_main.read_store = _MemoReadStore(
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
