"""Tests for acceptance/probes/meeting-review-disposition and isolated dev acceptance round-trip.

Covers:
1. Read-only hosted release probe validation against the recorded legacy 澄川 MT-1
   (mtg-2c016da5651a0dc4) shape before and after the MEETING-REVIEW-DISPOSITION-20261121 fix.
2. Exit 77 behavior when viewer credentials are absent, without leaking secrets.
3. Focused fixture and isolated dev acceptance write-and-readback round-trip across a
   process restart (ConsultationStore reload) via POST /bff/management/meetings/{meeting_id}/review,
   verifying that the separate nonfinancial dev acceptance record leaves pending in the
   Human Inbox while the user's real legacy meeting (mtg-2c016da5651a0dc4) is never touched.

Focused command for isolated dev acceptance write and readback:
  pytest -q acceptance/test_meeting_review_disposition.py -k isolated_dev_acceptance_write_and_readback
"""
from __future__ import annotations

import copy
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from services.consultation import main as consultation_main
from services.consultation.store import ConsultationStore
from services.consultation.workflow_state import WorkflowStateStore
from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.core.app_factory import build_bff_app
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models.service import ManagementService, _snapshot_meta
from services.control_plane.bff.meetings.router import create_meetings_router
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.personas.routes.common import run_management_read
from services.runtime_auth_inbound import encode_jwt_hs256

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "meeting-review-disposition"

_loader = SourceFileLoader("meeting_review_disposition_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)

LEGACY_USER_MEETING_ID = "mtg-2c016da5651a0dc4"
ISOLATED_ACCEPTANCE_MEETING_ID = "mtg-dev-acceptance-review-disposition-20261121"
TENANT = "tenant-dev"
JWT_SECRET = "meeting-review-disposition-acceptance-test-secret"
CONSULT_SERVICE_TOKEN = "meeting-review-disposition-consult-service-token"


# Recorded legacy shape of 澄川 MT-1 (mtg-2c016da5651a0dc4, closed 2026-11-20T17:40:52Z):
# two participants requested more research twice, zero proposals, stored synthesis
# 'Synthesis empty or unreadable; recorded as REVIEW with no proposal.' with null unreadable_reason.
LEGACY_CHENGCHUAN_MT1_FIXTURE: Dict[str, Any] = {
    "meeting_id": LEGACY_USER_MEETING_ID,
    "tenant_id": TENANT,
    "meeting_type": "MT-1",
    "meeting_code": "MT-1",
    "meeting_slug": "weekly_pool_evolution_review",
    "topic": "Pool evolution review for persona-2448c7053e33ea48da47 (2026-W47)",
    "status": "closed",
    "outcome": "REVIEW",
    "chair_persona_id": "persona-2448c7053e33ea48da47",
    "convened_by": {"kind": "persona", "id": "persona-2448c7053e33ea48da47"},
    "subject_ref": "persona:persona-2448c7053e33ea48da47",
    "attribution_record_id": None,
    "attribution_record": None,
    "participants": [
        {
            "persona_id": "persona-2448c7053e33ea48da47",
            "role": "chair",
            "style": "momentum",
            "origin": "institutional",
            "trading": True,
            "can_sponsor": True,
        },
        {
            "persona_id": "persona-risk-officer",
            "role": "risk_reviewer",
            "style": "risk_governance",
            "origin": "institutional",
            "trading": False,
            "can_sponsor": False,
        },
    ],
    "round": 2,
    "created_at": "2026-11-20T17:38:10Z",
    "closed_at": "2026-11-20T17:40:52Z",
    "iso_week": "2026-W47",
    "agenda": ["Pool evolution review for persona-2448c7053e33ea48da47 (2026-W47)"],
    "case_file_items": [
        {
            "item_id": "facts:pool:persona-2448c7053e33ea48da47:v1",
            "group": "facts",
            "kind": "pool_snapshot",
            "source_owner": "persona",
            "source_id": "facts:pool:persona-2448c7053e33ea48da47:v1",
            "excerpt": "Pool v1 active member reg-momentum-base (weight=1.0)",
        }
    ],
    "pre_read": [],
    "rounds": [
        {
            "round": 1,
            "positions": [
                {
                    "label": "role:chair|style:momentum#1",
                    "persona_id": "persona-2448c7053e33ea48da47",
                    "role": "chair",
                    "style": "momentum",
                    "stance": "request_more_research",
                    "confidence": 0.62,
                    "citations": ["facts:pool:persona-2448c7053e33ea48da47:v1"],
                    "changed": False,
                    "accepted": [],
                    "disputed": [],
                    "proposals": [],
                    "summary": "Request more research on member rule diversification before proposing pool mutation.",
                },
                {
                    "label": "role:risk_reviewer|style:risk_governance#2",
                    "persona_id": "persona-risk-officer",
                    "role": "risk_reviewer",
                    "style": "risk_governance",
                    "stance": "request_more_research",
                    "confidence": 0.71,
                    "citations": ["facts:pool:persona-2448c7053e33ea48da47:v1"],
                    "changed": False,
                    "accepted": [],
                    "disputed": [],
                    "proposals": [],
                    "summary": "Insufficient candidate breadth; request more research before changing pool weights.",
                },
            ],
        },
        {
            "round": 2,
            "positions": [
                {
                    "label": "role:chair|style:momentum#1",
                    "persona_id": "persona-2448c7053e33ea48da47",
                    "role": "chair",
                    "style": "momentum",
                    "stance": "request_more_research",
                    "confidence": 0.65,
                    "citations": ["facts:pool:persona-2448c7053e33ea48da47:v1"],
                    "changed": False,
                    "accepted": ["role:risk_reviewer|style:risk_governance#2"],
                    "disputed": [],
                    "proposals": [],
                    "summary": "Maintain current pool and request further research.",
                },
                {
                    "label": "role:risk_reviewer|style:risk_governance#2",
                    "persona_id": "persona-risk-officer",
                    "role": "risk_reviewer",
                    "style": "risk_governance",
                    "stance": "request_more_research",
                    "confidence": 0.74,
                    "citations": ["facts:pool:persona-2448c7053e33ea48da47:v1"],
                    "changed": False,
                    "accepted": ["role:chair|style:momentum#1"],
                    "disputed": [],
                    "proposals": [],
                    "summary": "Concur on holding pool unchanged pending research.",
                },
            ],
        },
    ],
    "synthesis": {
        "proposals": [],
        "tally": {
            "voting": False,
            "binding": False,
            "by_stance": {"request_more_research": 2},
            "weighted_confidence": {"request_more_research": 1.39},
            "mean_confidence": 0.695,
            "participant_count": 2,
        },
        "dissent_memo_id": f"mem-dissent-{LEGACY_USER_MEETING_ID}",
        "outcome": "REVIEW",
        "unreadable_reason": None,
        "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
        "voting": False,
        "rejected_proposals": [],
    },
    "minutes": {
        "institutional_entry_id": f"mem-{LEGACY_USER_MEETING_ID}",
        "memo_id": f"mem-summary-{LEGACY_USER_MEETING_ID}",
        "dissent_memo_id": f"mem-dissent-{LEGACY_USER_MEETING_ID}",
        "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
    },
    "writebacks": [
        {"persona_id": "persona-2448c7053e33ea48da47", "entry_id": "pmem-chengchuan-1"},
        {"persona_id": "persona-risk-officer", "entry_id": "pmem-risk-1"},
    ],
    "usage": {"participants": 2, "rounds": 2, "calls": 5},
    "trace_id": f"trace-{LEGACY_USER_MEETING_ID}",
}

# Isolated nonfinancial dev acceptance fixture for write/readback verification
# without ever acting as the operator on the user's real meeting.
ISOLATED_DEV_ACCEPTANCE_MEETING_FIXTURE: Dict[str, Any] = {
    **copy.deepcopy(LEGACY_CHENGCHUAN_MT1_FIXTURE),
    "meeting_id": ISOLATED_ACCEPTANCE_MEETING_ID,
    "topic": "Isolated nonfinancial dev acceptance synthesis review check",
    "created_at": "2026-11-21T12:00:00Z",
    "closed_at": "2026-11-21T12:02:00Z",
    "synthesis_unreadable_reason": "model_output_missing",
    "synthesis": {
        **copy.deepcopy(LEGACY_CHENGCHUAN_MT1_FIXTURE["synthesis"]),
        "dissent_memo_id": f"mem-dissent-{ISOLATED_ACCEPTANCE_MEETING_ID}",
        "unreadable_reason": "model_output_missing",
        "summary": "Synthesis empty or unreadable (model_output_missing); recorded as REVIEW with no proposal.",
    },
    "minutes": {
        "institutional_entry_id": f"mem-{ISOLATED_ACCEPTANCE_MEETING_ID}",
        "memo_id": f"mem-summary-{ISOLATED_ACCEPTANCE_MEETING_ID}",
        "dissent_memo_id": f"mem-dissent-{ISOLATED_ACCEPTANCE_MEETING_ID}",
        "summary": "Synthesis empty or unreadable (model_output_missing); recorded as REVIEW with no proposal.",
    },
    "trace_id": f"trace-{ISOLATED_ACCEPTANCE_MEETING_ID}",
}


class _StubReadSurface:
    def list_approval_queue_items(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_governance_review_queue_items(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def list_incidents(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return []

    def dataset_source(self, _name: str) -> str:
        return "read_store"


def _jwt_headers(sub: str, roles: list[str], tenant: str = TENANT, idem_key: str | None = None) -> dict[str, str]:
    token = encode_jwt_hs256(
        {"sub": sub, "roles": roles, "tenant_id": tenant, "exp": 4102444800},
        secret=JWT_SECRET,
    )
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return headers


def test_probe_is_executable_with_owner_header() -> None:
    assert os.access(PROBE, os.X_OK)
    header_lines = "\n".join(PROBE.read_text(encoding="utf-8").splitlines()[:20])
    assert "# acceptance-owner: MEETING-REVIEW-DISPOSITION-20261121" in header_lines


def test_probe_fails_on_pre_fix_hosted_legacy_shape() -> None:
    """Before MEETING-REVIEW-DISPOSITION-20261121:
    - detail lacked operator_review, synthesis_unreadable_reason (was null), and evidence_refs;
    - human inbox item lacked unreadable_reason and evidence_refs.
    """
    pre_fix_detail = {
        "status": "ok",
        "owner": "consultation",
        "meeting_id": LEGACY_USER_MEETING_ID,
        "status_code": 200,
        "outcome": "REVIEW",
        "case_file_items": LEGACY_CHENGCHUAN_MT1_FIXTURE["case_file_items"],
        "rounds": LEGACY_CHENGCHUAN_MT1_FIXTURE["rounds"],
        "synthesis": {
            "proposals": [],
            "outcome": "REVIEW",
            "unreadable_reason": None,
            "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
            "dissent_memo_id": f"mem-dissent-{LEGACY_USER_MEETING_ID}",
        },
        "data": {
            "meeting_id": LEGACY_USER_MEETING_ID,
            "status": "closed",
            "outcome": "REVIEW",
            "case_file_items": LEGACY_CHENGCHUAN_MT1_FIXTURE["case_file_items"],
            "rounds": LEGACY_CHENGCHUAN_MT1_FIXTURE["rounds"],
            "synthesis": {
                "proposals": [],
                "outcome": "REVIEW",
                "unreadable_reason": None,
                "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
                "dissent_memo_id": f"mem-dissent-{LEGACY_USER_MEETING_ID}",
            },
        },
    }
    pre_fix_inbox = {
        "data": {
            "items": [
                {
                    "id": f"meeting_review:{LEGACY_USER_MEETING_ID}",
                    "kind": "meeting_review",
                    "meeting_id": LEGACY_USER_MEETING_ID,
                    "status": "pending",
                    "action_state": "pending",
                    "outcome": "REVIEW",
                    "reason": "synthesis_review_required",
                }
            ]
        },
        "meta": {"surfaces": {"meetings": {"status": "ok"}}},
    }

    def fetch(path: str) -> dict:
        if path.startswith("/bff/management/meetings?"):
            return {"status": "ok", "items": [{"meeting_id": LEGACY_USER_MEETING_ID, "status": "closed", "outcome": "REVIEW"}]}
        if path.startswith("/bff/management/human-inbox"):
            return pre_fix_inbox
        if path == f"/bff/management/meetings/{LEGACY_USER_MEETING_ID}":
            return pre_fix_detail
        raise AssertionError(f"Unexpected path: {path}")

    report = probe.check(fetch)
    assert report["passed"] is False
    failure_codes = {f.split(":")[0] for f in report["failed"]}
    assert {
        "operator_review_field_missing",
        "unreadable_reason_missing",
        "evidence_refs_missing",
        "inbox_reason_missing",
    } <= failure_codes


def test_probe_exit_77_on_missing_env_without_network_or_secret_leak() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:9"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 77
    assert "ACCEPTANCE_VIEWER_CLIENT_ID" in done.stdout

    env["ACCEPTANCE_VIEWER_CLIENT_ID"] = "viewer"
    env["ACCEPTANCE_VIEWER_CLIENT_SECRET"] = "top-secret-viewer-token"
    done_fail = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=30)
    assert done_fail.returncode == 1
    assert "top-secret-viewer-token" not in (done_fail.stdout + done_fail.stderr)


def test_isolated_dev_acceptance_write_and_readback_leaves_legacy_user_meeting_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end round-trip through BFF -> Consultation owner + process restart reload:
    - Seeds both the user's real legacy meeting (mtg-2c016da5651a0dc4) and the separate
      isolated nonfinancial dev acceptance meeting (mtg-dev-acceptance-review-disposition-20261121).
    - Verifies the read-only probe passes before review (legacy cause is 'unknown', specific
      cause is 'model_output_missing', both pending in human inbox with evidence_refs).
    - Reviews ONLY the isolated dev acceptance meeting via POST /bff/management/meetings/{id}/review.
    - Reloads ConsultationStore from disk (simulating consultation-svc restart).
    - Verifies the isolated meeting's operator_review survives restart and leaves the pending
      Human Inbox while the original REVIEW synthesis, transcript/rounds, and zero proposals
      remain intact, and the user's real legacy meeting mtg-2c016da5651a0dc4 remains completely
      untouched and pending.
    """
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_BFF_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_BFF_JWT_AUDIENCE", "")
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", TENANT)
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", CONSULT_SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_TENANT_ID", TENANT)
    monkeypatch.setenv("PANTHEON_CONSULTATION_API_URL", "http://consultation.acceptance.local")
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research.acceptance.local")

    data_dir = tmp_path / "consultation_data"
    wf_path = tmp_path / "wf.sqlite3"
    consultation_main.store = ConsultationStore(str(data_dir))
    consultation_main.workflow_state = WorkflowStateStore(wf_path)
    consultation_main.store.put_meeting(copy.deepcopy(LEGACY_CHENGCHUAN_MT1_FIXTURE))
    consultation_main.store.put_meeting(copy.deepcopy(ISOLATED_DEV_ACCEPTANCE_MEETING_FIXTURE))

    consult_client = TestClient(consultation_main.app)
    research_calls = []

    def bridge_urlopen(req, timeout=5):
        u = urlsplit(req.full_url)
        if u.hostname == "research.acceptance.local":
            # Exercise the real Research client and caller context, returning an
            # authorized empty page rather than hiding an unconfigured owner.
            headers = {k.lower(): v for k, v in req.header_items()}
            query = parse_qs(u.query)
            research_calls.append((req.method, u.path, query, headers))
            assert req.method == "GET"
            assert u.path == "/api/research/tickets"
            assert headers["authorization"] == viewer_headers["Authorization"]
            assert headers["x-tenant-id"] == TENANT
            assert query == {
                "meeting_linked": ["true"],
                "human_actionable": ["true"],
                "page_size": ["100"],
            }
            out = io.BytesIO(json.dumps({
                "data": [],
                "page_info": {"page_size": 100, "next_page_token": None},
            }).encode("utf-8"))
            out.status = 200
            return out
        assert u.hostname == "consultation.acceptance.local", f"Unexpected host: {req.full_url}"
        path_With_query = u.path + (f"?{u.query}" if u.query else "")
        hdrs = {k: v for k, v in req.header_items()}
        raw_body = req.data
        resp = consult_client.request(
            req.method,
            path_With_query,
            headers=hdrs,
            content=raw_body,
        )
        if resp.status_code >= 400:
            raise HTTPError(
                req.full_url,
                resp.status_code,
                resp.reason_phrase,
                {},
                io.BytesIO(resp.content),
            )
        out = io.BytesIO(resp.content)
        out.status = resp.status_code
        return out

    monkeypatch.setattr("urllib.request.urlopen", bridge_urlopen)

    read_surface = _StubReadSurface()
    mgmt_service = ManagementService(get_read_store=lambda: read_surface)
    bff_app = build_bff_app()
    bff_app.add_middleware(OwnerReadContextMiddleware)
    bff_app.include_router(
        create_meetings_router(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
        )
    )
    bff_app.include_router(
        create_management_router(
            read_surface=read_surface,
            get_read_store=lambda: read_surface,
            service=mgmt_service,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            snapshot_meta=_snapshot_meta,
            utc_now=utc_now,
            bff_error=bff_error,
            run_management_read=run_management_read,
        )
    )
    bff_client = TestClient(bff_app)
    viewer_headers = _jwt_headers("viewer-probe", ["viewer"])
    operator_headers = _jwt_headers("op-acceptance", ["operator"], idem_key="idem-isolated-review-1")

    def bff_fetch(path: str) -> dict:
        r = bff_client.get(path, headers=viewer_headers)
        assert r.status_code == 200, (path, r.text)
        return r.json()

    # 1. Before review: probe passes; legacy meeting exposes cause='unknown' & evidence_refs; both pending in inbox
    report_before = probe.check(bff_fetch)
    assert report_before["passed"] is True, report_before
    assert report_before["pending_review_count"] == 2
    assert report_before["reviewed_count"] == 0

    legacy_before = bff_fetch(f"/bff/management/meetings/{LEGACY_USER_MEETING_ID}")["data"]
    assert legacy_before["operator_review"] is None
    assert legacy_before["synthesis_unreadable_reason"] == "unknown"
    assert legacy_before["synthesis"]["unreadable_reason"] == "unknown"
    assert "facts:pool:persona-2448c7053e33ea48da47:v1" in legacy_before["evidence_refs"]

    isolated_before = bff_fetch(f"/bff/management/meetings/{ISOLATED_ACCEPTANCE_MEETING_ID}")["data"]
    assert isolated_before["operator_review"] is None
    assert isolated_before["synthesis_unreadable_reason"] == "model_output_missing"

    # 2. Viewer role is rejected on the review write route
    viewer_write = bff_client.post(
        f"/bff/management/meetings/{ISOLATED_ACCEPTANCE_MEETING_ID}/review",
        headers=viewer_headers,
        json={"note": "Viewer must not be able to review"},
    )
    assert viewer_write.status_code == 403

    # 3. Authorized operator reviews ONLY the isolated dev acceptance meeting
    note_text = (
        "Isolated dev acceptance review: participants requested more research twice; "
        "closed review with zero proposals."
    )
    review_resp = bff_client.post(
        f"/bff/management/meetings/{ISOLATED_ACCEPTANCE_MEETING_ID}/review",
        headers=operator_headers,
        json={"note": note_text},
    )
    assert review_resp.status_code == 200, review_resp.text
    review_body = review_resp.json()
    assert review_body["replayed"] is False
    assert review_body["operator_review"]["status"] == "reviewed"
    assert review_body["operator_review"]["disposition"] == "closed_no_proposal"
    assert review_body["operator_review"]["note"] == note_text
    assert review_body["operator_review"]["reviewed_by"] == "op-acceptance"
    assert review_body["operator_review"]["cause"] == "model_output_missing"

    # 4. Simulate process restart by instantiating a brand-new ConsultationStore on data_dir
    consultation_main.store = ConsultationStore(str(data_dir))

    # 5. Read back after restart and re-run the read-only hosted probe
    report_after = probe.check(bff_fetch)
    assert report_after["passed"] is True, report_after
    assert report_after["pending_review_count"] == 1
    assert report_after["reviewed_count"] == 1
    assert report_after["results"] == {
        LEGACY_USER_MEETING_ID: "pending_review",
        ISOLATED_ACCEPTANCE_MEETING_ID: "reviewed",
    }

    # Verify isolated meeting persisted review and kept original REVIEW synthesis, rounds, and 0 proposals
    isolated_after = bff_fetch(f"/bff/management/meetings/{ISOLATED_ACCEPTANCE_MEETING_ID}")["data"]
    assert isolated_after["status"] == "closed"
    assert isolated_after["outcome"] == "REVIEW"
    assert isolated_after["synthesis"]["outcome"] == "REVIEW"
    assert isolated_after["synthesis"]["proposals"] == []
    assert len(isolated_after["rounds"]) == 2
    assert isolated_after["operator_review"]["status"] == "reviewed"
    assert isolated_after["operator_review"]["note"] == note_text
    assert isolated_after["operator_review"]["reviewed_by"] == "op-acceptance"
    assert isolated_after["operator_review"]["reviewed_at"] == review_body["operator_review"]["reviewed_at"]

    # Verify the user's real legacy meeting mtg-2c016da5651a0dc4 was never touched and remains pending in inbox
    legacy_after = bff_fetch(f"/bff/management/meetings/{LEGACY_USER_MEETING_ID}")["data"]
    assert legacy_after["operator_review"] is None
    assert legacy_after["outcome"] == "REVIEW"
    assert legacy_after["synthesis"]["summary"] == "Synthesis empty or unreadable; recorded as REVIEW with no proposal."
    inbox_after = bff_fetch("/bff/management/human-inbox?source_type=meeting")["data"]["items"]
    assert [i["id"] for i in inbox_after] == [f"meeting_review:{LEGACY_USER_MEETING_ID}"]
    # Two probe reads and the final reload each perform one bounded owner read.
    assert len(research_calls) == 3
