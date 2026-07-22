from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from read_store import ReadSurfaceStore


class _FakeConsultationClient:
    instances = []

    @classmethod
    def configured(cls) -> bool:
        return True

    def __init__(self, *, timeout_seconds=None):
        self.timeout_seconds = timeout_seconds
        self.created_payloads = []
        self.cancel_calls = []
        self.sponsor_calls = []
        self.requests = [
            {
                "request_id": "cr-service-committee-001",
                "request_type": "execution_risk",
                "requested_by": {"actor_type": "operator", "actor_id": "operator-service"},
                "from_persona_id": "persona-alpha",
                "target_type": "deployment_plan",
                "target_id": "plan-service-001",
                "task": "Review service-backed committee handoff.",
                "consultation_type": "risk_review",
                "evidence_refs": ["ev-service-001"],
                "priority": "normal",
                "status": "in_progress",
                "linked_session_id": "cs-service-committee-001",
                "request_to_session_status": "session_running",
                "trace_id": "trace-service-committee-001",
                "created_at": "2026-04-20T06:00:00Z",
                "metadata": {
                    "consultation": {
                        "requester_session_id": "cs-service-committee-001",
                        "committee_session_ids": ["cm-service-committee-001"],
                        "committee_ref": "committee-service-001",
                        "quorum_state": "quorum_met",
                        "consensus_state": "sponsor_required",
                        "committee_started_at": "2026-04-20T06:01:00Z",
                        "sponsor_session_id": "cm-service-committee-001",
                        "sponsor_decision": None,
                        "sponsor_decided_at": None,
                        "sponsor_decided_by": None,
                        "synthesis_summary": {"outcome": "pending", "evidence_refs": ["ev-service-001"]},
                        "evidence_refs": [{"id": "ev-service-001"}],
                        "committee_participants": [
                            {
                                "session_id": "cm-service-committee-001",
                                "persona_id": "p-compliance-sponsor",
                                "role": "sponsor",
                                "status": "active",
                            }
                        ],
                    }
                },
            }
        ]
        self.memos = []
        self.handoffs = []
        type(self).instances.append(self)

    def list_requests(self):
        return [dict(request) for request in self.requests]

    def create_request(self, payload):
        self.created_payloads.append(payload)
        created = {
            **payload,
            "request_id": "cr-http-001",
            "status": "draft",
        }
        self.requests.append(created)
        return dict(created)

    def cancel_request(self, request_id, *, actor_id, canceled_at=None):
        self.cancel_calls.append((request_id, actor_id, canceled_at))
        for request in self.requests:
            if request["request_id"] == request_id:
                request["status"] = "cancelled"
                request["canceled_at"] = canceled_at
                request["request_to_session_status"] = "canceled_before_session"
                request["session_handoff_note"] = "Request canceled by operator."
                return dict(request)
        return None

    def list_handoffs(self, *, request_id=None):
        handoffs = self.handoffs
        if request_id:
            handoffs = [handoff for handoff in handoffs if handoff["request_id"] == request_id]
        return [dict(handoff) for handoff in handoffs]

    def list_transcripts(self):
        return []

    def list_memos(self):
        return [dict(memo) for memo in self.memos]

    def get_request(self, request_id):
        for request in self.requests:
            if request["request_id"] == request_id:
                return dict(request)
        return None

    def record_sponsor_decision(self, committee_id, *, sponsor_decision, rationale_ref, actor_id, recorded_at=None):
        self.sponsor_calls.append((committee_id, sponsor_decision, rationale_ref, actor_id, recorded_at))
        for request in self.requests:
            consult = request["metadata"]["consultation"]
            if consult["committee_ref"] == committee_id:
                consult["sponsor_decision"] = sponsor_decision
                consult["sponsor_decided_at"] = recorded_at
                consult["sponsor_decided_by"] = actor_id
                consult["consensus_state"] = "reached"
                consult["synthesis_summary"]["rationale_ref"] = rationale_ref
                handoff = {
                    "handoff_id": "gh-http-001",
                    "request_id": request["request_id"],
                    "target_gate": f"committee_sponsor_decision:{committee_id}",
                    "memo_ids": ["mem-http-001"],
                    "evidence_refs": ["ev-service-001"],
                    "audit_refs": ["aud-http-001"],
                    "status": "sent",
                }
                consult["service_handoff"] = {
                    "handoff_id": handoff["handoff_id"],
                    "target_gate": handoff["target_gate"],
                    "evidence_refs": handoff["evidence_refs"],
                    "audit_refs": handoff["audit_refs"],
                    "status": "sent",
                }
                self.handoffs.append(handoff)
                return {
                    "committee_id": committee_id,
                    "sponsor_decision": sponsor_decision,
                    "service_handoff": consult["service_handoff"],
                }
        raise AssertionError("committee not found")


def test_service_session_projection_preserves_runtime_lifecycle_and_freshness_truth() -> None:
    raw = {
        "id": "legacy-session-id",
        "session_id": "session-authoritative-001",
        "persona_id": "persona-alpha",
        "session_type": "paper_runtime",
        "status": "ended",
        "state": "degraded",
        "lifecycle_state": "terminal",
        "active": False,
        "runtime_binding_id": "rb-authoritative-001",
        "binding_id": "rb-authoritative-001",
        "runtime_id": "runtime-authoritative-001",
        "runtime_identity": {
            "runtime_id": "runtime-authoritative-001",
            "runtime_binding_id": "rb-authoritative-001",
            "deployment_stage": "paper",
        },
        "runtime_kind": "lean_runtime",
        "deployment_stage": "paper",
        "deployment_mode": "paper",
        "created_at": "2026-07-13T11:59:00Z",
        "started_at": "2026-07-13T12:00:00Z",
        "last_heartbeat_at": "2026-07-13T12:04:00Z",
        "last_seen_at": "2026-07-13T12:04:01Z",
        "updated_at": "2026-07-13T12:06:00Z",
        "ended_at": "2026-07-13T12:05:31Z",
        "heartbeat_status": "stale",
        "stale": True,
        "stale_at": "2026-07-13T12:05:30Z",
        "stale_after_seconds": 90,
        "staleness": {
            "status": "stale",
            "reason": "stale_heartbeat",
            "age_seconds": 91,
            "threshold_seconds": 90,
        },
        "degraded": True,
        "degraded_at": "2026-07-13T12:05:30Z",
        "reason": "runtime_session_unavailable",
        "ended_reason": "stale_monitoring_session",
        "terminal_reason": "stale_heartbeat",
        "degraded_reasons": ["heartbeat_expired"],
        "last_error": {"code": "heartbeat_expired"},
        "metadata": {"source": "runtime-manager"},
    }

    projected = ReadSurfaceStore._project_service_session(raw)

    assert projected["id"] == "session-authoritative-001"
    assert projected["session_id"] == "session-authoritative-001"
    for field in (
        "runtime_binding_id",
        "binding_id",
        "runtime_id",
        "runtime_identity",
        "runtime_kind",
        "deployment_stage",
        "deployment_mode",
        "status",
        "state",
        "lifecycle_state",
        "active",
        "created_at",
        "started_at",
        "last_heartbeat_at",
        "last_seen_at",
        "updated_at",
        "ended_at",
        "heartbeat_status",
        "stale",
        "stale_at",
        "stale_after_seconds",
        "staleness",
        "degraded",
        "degraded_at",
        "reason",
        "ended_reason",
        "terminal_reason",
        "degraded_reasons",
        "last_error",
    ):
        assert projected[field] == raw[field]

    projected["runtime_identity"]["runtime_id"] = "mutated"
    projected["staleness"]["status"] = "fresh"
    projected["metadata"]["source"] = "mutated"
    assert raw["runtime_identity"]["runtime_id"] == "runtime-authoritative-001"
    assert raw["staleness"]["status"] == "stale"
    assert raw["metadata"]["source"] == "runtime-manager"


def test_service_session_projection_does_not_invent_optional_freshness_state() -> None:
    projected = ReadSurfaceStore._project_service_session(
        {
            "session_id": "session-no-freshness-001",
            "persona_id": "persona-alpha",
            "status": "active",
            "started_at": "2026-07-13T12:00:00Z",
            "runtime_binding_id": "rb-authoritative-001",
            "runtime_id": "runtime-authoritative-001",
        }
    )

    assert "last_heartbeat_at" not in projected
    assert "updated_at" not in projected
    assert "staleness" not in projected
    assert "stale" not in projected
    assert "degraded" not in projected
    assert "reason" not in projected


def test_runtime_binding_projection_does_not_infer_deployment_mode() -> None:
    projected = ReadSurfaceStore._project_canonical_runtime_binding(
        {
            "binding_id": "runtime-binding-with-legacy-stage",
            "runtime_id": "runtime-with-legacy-stage",
            "status": "active",
            "state": "running",
            "deployment_stage": "live",
            "runtime_kind": "live",
        }
    )

    assert projected["deployment_stage"] == "live"
    assert projected["runtime_kind"] == "live"
    assert projected["deployment_mode"] is None


def test_governance_runtime_and_evidence_reads_use_http_service_clients_without_snapshot_fallback() -> None:
    responses = {
        ("http://deployment:8095", "/api/deployment/plans"): [
            {
                "plan_id": "plan-svc-001",
                "approval_decision_id": "approval-svc-001",
                "target_stage": "paper",
                "status": "approved",
                "artifact_id": "artifact-svc-001",
                "capital_pool_id": "pool-svc-001",
            }
        ],
        ("http://governance:8082", "/api/governance/approvals"): [
            {
                "decision_id": "approval-svc-001",
                "outcome": "approved",
                "decision_state": "decided",
                "actor_id": "risk-committee",
                "risk_level": "medium",
            }
        ],
        ("http://capital:8092", "/api/capital-pools"): [
            {"pool_id": "pool-svc-001", "name": "Service Pool", "status": "active"}
        ],
        ("http://capital:8092", "/api/bindings"): [
            {
                "binding_id": "pcb-svc-001",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-svc-001",
                "status": "active",
            }
        ],
        ("http://runtime-manager:8081", "/api/runtime-bindings"): {
            "bindings": [
                {
                    "binding_id": "rb-svc-001",
                    "runtime_id": "runtime-svc-001",
                    "plan_id": "plan-svc-001",
                    "deployment_mode": "paper",
                    "status": "active",
                }
            ]
        },
        ("http://lineage-read:8094", "/api/v1/lineage"): [
            {
                "id": "edge-svc-001",
                "source_type": "StrategySpec",
                "source_id": "strat-svc-001",
                "target_type": "CandidateArtifact",
                "target_id": "artifact-svc-001",
                "artifact_id": "artifact-svc-001",
                "created_at": "2026-04-28T00:00:00Z",
            }
        ],
    }

    def fake_get(base_url: str, path: str, *, headers=None):
        if base_url == "http://runtime-manager:8081":
            assert headers == {"Authorization": "Bearer runtime-control-internal"}
        return True, responses[(base_url, path)]

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_DEPLOYMENT_API_URL": "http://deployment:8095",
                "PANTHEON_GOVERNANCE_APPROVAL_API_URL": "http://governance:8082",
                "PANTHEON_CAPITAL_API_URL": "http://capital:8092",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_LINEAGE_READ_URL": "http://lineage-read:8094",
                "PANTHEON_GOVERNANCE_DATA_DIR": "",
                "PANTHEON_RUNTIME_DATA_DIR": "",
                "BFF_DATA_DIR": td,
            },
            clear=False,
        ):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )

                plan = store.get_deployment_plan("plan-svc-001")
                assert plan is not None
                assert plan["runtime_binding_id"] == "rb-svc-001"

                decision = store.get_approval_decision("approval-svc-001")
                assert decision is not None
                assert decision["reviewer"] == "risk-committee"

                assert store.get_capital_pool("pool-svc-001")["name"] == "Service Pool"
                assert store.get_runtime_binding("rb-svc-001")["runtime_id"] == "runtime-svc-001"
                assert store.list_lineage_edges("artifact-svc-001")[0]["id"] == "edge-svc-001"
                assert store.dataset_source("deployment_plans") == "service_client"
                assert store.dataset_source("lineage_edges") == "service_client"


def test_paper_runtime_monitoring_staleness_marker_is_not_active() -> None:
    responses = {
        ("http://paper-fleet:8011", "/api/fleet/state"): {
            "monitoring_sessions": [
                {
                    "session_id": "prmon-stale-marker",
                    "session_type": "paper_runtime_monitoring",
                    "binding_id": "rtb-stale-marker",
                    "runtime_binding_id": "rtb-stale-marker",
                    "runtime_id": "runtime-stale-marker",
                    "deployment_stage": "paper",
                    "status": "running",
                    "active": True,
                    "started_at": "2026-06-09T00:00:00Z",
                    "ended_at": None,
                    "last_heartbeat_at": "2026-06-09T00:00:00Z",
                    "staleness": {
                        "status": "stale",
                        "reason": "stale_heartbeat",
                        "last_known_at": "2026-06-09T00:00:00Z",
                        "age_seconds": 600,
                        "threshold_seconds": 90,
                    },
                }
            ],
        }
    }

    def fake_get(base_url: str, path: str, *, headers=None):
        return True, responses[(base_url, path)]

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_PAPER_FLEET_RECONCILER_URL": "http://paper-fleet:8011",
                "PANTHEON_PAPER_RUNTIME_MONITORING_URL": "",
                "PANTHEON_RUNTIME_DATA_DIR": "",
                "BFF_DATA_DIR": td,
            },
            clear=False,
        ):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )

                session = store.get_paper_runtime_monitoring_session(
                    runtime_id="runtime-stale-marker",
                    binding_id="rtb-stale-marker",
                )

                assert session is not None
                assert session["active"] is False
                assert session["staleness"]["reason"] == "stale_heartbeat"
                assert store.dataset_source("paper_runtime_monitoring_sessions") == "service_client"


def test_snapshot_payload_does_not_mask_missing_service_client_data_when_fallback_disabled() -> None:
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_DEPLOYMENT_API_URL": "",
                "PANTHEON_DEPLOYMENT_SERVICE_URL": "",
                "PANTHEON_GOVERNANCE_DATA_DIR": "",
                "BFF_DATA_DIR": td,
            },
            clear=False,
        ):
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )

            assert store.list_deployment_plans() == []
            assert store.dataset_source("deployment_plans") == "missing"


def test_memory_reads_use_http_service_client_when_url_configured() -> None:
    responses = {
        ("http://memory:8086", "/api/memory/entries"): {
            "entries": [
                {
                    "entry_id": "mem-svc-001",
                    "knowledge_type": "research_finding",
                    "content": {"headline": "Service-backed memory entry", "tags": ["memory", "service"]},
                    "source_event_type": "research_task_completed",
                    "source_event_id": "research-task-svc-001",
                    "scope": "strategy_family",
                    "scope_filter": "momentum",
                    "reuse_count": 4,
                    "superseded_by": "mem-svc-002",
                    "written_at": "2026-04-20T06:00:00Z",
                    "write_authority": "memory-service",
                    "contributing_persona_ids": ["persona-alpha"],
                }
            ],
            "count": 1,
        }
    }

    def fake_get(base_url: str, path: str, *, headers=None):
        return True, responses[(base_url, path)]

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_MEMORY_API_URL": "http://memory:8086",
                "PANTHEON_MEMORY_DATA_DIR": "",
                "BFF_DATA_DIR": td,
            },
            clear=False,
        ):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )

                entries = store.list_institutional_memory_entries()
                assert [entry["entry_id"] for entry in entries] == ["mem-svc-001"]
                assert entries[0]["headline"] == "Service-backed memory entry"
                assert entries[0]["scope"] == "strategy_family"
                assert entries[0]["scope_filter"] == "momentum"
                assert entries[0]["reuse_count"] == 4
                assert entries[0]["is_superseded"] is True

                detail = store.get_institutional_memory_entry("mem-svc-001")
                assert detail["source_event"] == {
                    "type": "research_task_completed",
                    "id": "research-task-svc-001",
                }
                assert detail["scope"] == {"type": "strategy_family", "filter": "momentum"}
                assert detail["lifecycle"] == {"status": "superseded", "superseded_by": "mem-svc-002"}
                assert detail["usage"]["reuse_count"] == 4
                assert store.dataset_source("institutional_memory_entries") == "service_client"


def test_consultation_reads_and_writes_use_http_service_client_when_url_configured() -> None:
    _FakeConsultationClient.instances = []
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_CONSULTATION_API_URL": "http://consultation-svc:8096",
                "PANTHEON_BFF_CONSULTATION_DATA_DIR": "",
                "BFF_DATA_DIR": td,
            },
            clear=False,
        ):
            with mock.patch("read_store.ConsultationServiceClient", _FakeConsultationClient):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                created = store.create_consult_request(
                    from_persona_id="persona-alpha",
                    target_type="persona",
                    target_ref="persona-beta",
                    task="Review beta persona risk.",
                    context_refs=[{"type": "deployment_plan", "id": "plan-http-001"}],
                    priority="high",
                    consultation_type="risk_review",
                    actor_id="operator-http",
                    created_at="2026-04-20T06:00:00Z",
                )
                assert created["request_id"] == "cr-http-001"
                assert created["status"] == "created"
                fake = _FakeConsultationClient.instances[0]
                assert fake.created_payloads[0]["target_id"] == "persona-beta"
                assert fake.created_payloads[0]["metadata"]["bff_context_refs"][0]["id"] == "plan-http-001"

                assert store.dataset_source("consult_requests") == "consultation_service_client"
                rows = store.list_consult_requests()
                assert {row["request_id"] for row in rows} >= {"cr-http-001", "cr-service-committee-001"}

                canceled = store.cancel_consult_request(
                    "cr-http-001",
                    actor_id="operator-http",
                    canceled_at="2026-04-20T06:05:00Z",
                )
                assert canceled is not None
                assert canceled["status"] == "canceled"
                assert fake.cancel_calls == [("cr-http-001", "operator-http", "2026-04-20T06:05:00Z")]

                updated = store.record_sponsor_decision(
                    "committee-service-001",
                    sponsor_decision="conditional",
                    rationale_ref="workspace://committee-rationales/http/final",
                    actor_id="operator-http",
                    recorded_at="2026-04-20T06:10:00Z",
                )
                assert updated is not None
                assert updated["sponsor_decision"] == "conditional"
                assert updated["service_handoff"]["handoff_id"] == "gh-http-001"
                assert fake.sponsor_calls == [
                    (
                        "committee-service-001",
                        "conditional",
                        "workspace://committee-rationales/http/final",
                        "operator-http",
                        "2026-04-20T06:10:00Z",
                    )
                ]
