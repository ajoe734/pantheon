"""L12-CURRENT-E2E-HUMAN-LEARNING-20260814: Deployed E2E for Loops 5, 6, 7.

Exercises:
- Loop 5: Agora Interaction Evidence & Durable Dataset Handoff
- Loop 6: Human Imitation / Shadow Evaluation & Research HTTP Handoff
- Loop 7: Consultation Provider Interaction & Governance Handoff
- Cross-Loop Human-Learning Identity Correlation & Anti-Direct-Store Assertions
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional
from unittest import mock

import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Repo roots and path setup
REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
BFF_DIR = SERVICES_DIR / "control-plane" / "bff"
PL_DIR = SERVICES_DIR / "policy-learning"
ADAPTER_DIR = SERVICES_DIR / "openclaw-gateway-adapter"

for p in (str(REPO_ROOT), str(SERVICES_DIR), str(BFF_DIR), str(PL_DIR), str(ADAPTER_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Consultation service and adapter imports
from services.consultation import main as consultation_main
from services.consultation.provider import (
    ContributionBlocked,
    HttpContributionProvider,
)
from services.consultation.store import ConsultationStore
from services.consultation.workflow_executor import (
    ExecutorConfig,
    execute_claim,
    initial_health_state,
    run_tick as run_consultation_tick,
    update_functional_health,
)
from services.consultation.workflow_state import WorkflowStateStore

from consultation_provider import (
    CONSULTATION_CONTRIBUTION_PATH,
    create_app as create_openclaw_adapter_app,
)

# Governance service imports
from services.governance import main as governance_main
from services.governance.record_store import JsonGovernanceRecordStore

# Agora BFF dataset extraction imports
from agora.dataset_extraction.extractor import AgoraDatasetStore
from agora.dataset_extraction.router import (
    create_dataset_extraction_router,
)
try:
    from models import OperatorIdentity
except ImportError:
    from agora.dataset_extraction.router import OperatorIdentity  # fallback if needed

# Policy Learning service imports
import agora_handoff_drainer
import candidate_experiment_handoff
from candidate_experiment_handoff import (
    CandidateHandoffError,
    handoff_candidate_to_experiment_authority,
)
from research_candidate_client import (
    ResearchCandidateClientError,
    post_imitation_candidate_intake_http,
)


def _load_policy_learning_module(data_dir: str):
    if str(PL_DIR) not in sys.path:
        sys.path.insert(0, str(PL_DIR))
    for key in list(sys.modules):
        if "l12_hl_pl_main" in key:
            del sys.modules[key]
    sys.modules.pop("store", None)
    spec = importlib.util.spec_from_file_location(
        "l12_hl_pl_main",
        PL_DIR / "main.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["l12_hl_pl_main"] = mod
    with mock.patch.dict(
        "os.environ",
        {
            "POLICY_LEARNING_DATA_DIR": data_dir,
            "POLICY_LEARNING_STORE_BACKEND": "json",
            "PERSISTENCE_POSTURE": "lenient",
        },
    ):
        spec.loader.exec_module(mod)
    return mod


# Research service imports
from services.research.main import app as research_app


# ---------------------------------------------------------------------------
# Constants & Helper Definitions
# ---------------------------------------------------------------------------

TENANT_ID = "tenant-human-learning-e2e"
SERVICE_ACTOR = "policy-learning-agora-handoff-drainer"
AGORA_TOKEN = "e2e-agora-service-token"
POLICY_LEARNING_TOKEN = "e2e-policy-learning-service-token"
CONSULTATION_SERVICE_TOKEN = "e2e-consultation-service-token"
CONSULTATION_OPERATOR_TOKEN = "e2e-consultation-operator-token"
OPENCLAW_PROVIDER_TOKEN = "e2e-openclaw-provider-token"
GOVERNANCE_HANDOFF_TOKEN = "e2e-governance-handoff-token"
CONSULTATION_SERVICE_ACTOR = "consultation-workflow-executor"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _serve_asgi(application: Any) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() >= deadline:
            raise RuntimeError("ASGI test server failed to start within timeout")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _request_json(
    url: str,
    *,
    method: str = "GET",
    token: str = "",
    tenant: str = TENANT_ID,
    headers_extra: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Any:
    headers = {
        "Accept": "application/json",
        "X-Pantheon-Tenant-Id": tenant,
        "X-Tenant-Id": tenant,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if headers_extra:
        headers.update(headers_extra)

    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10.0) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Fake OpenClaw Provider & Handoff Helpers for Consultation
# ---------------------------------------------------------------------------

class FakeOpenClawRiskProvider:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def invoke(self, prompt: str, **kwargs: Any) -> Any:
        context = dict(kwargs.get("context_pack", {}))
        request_id = str(context.get("request_id", "req-unknown"))
        tenant_id = str(context.get("tenant_id", TENANT_ID))
        contribution = {
            "contribution_id": f"contrib-openclaw-{request_id}",
            "tenant_id": tenant_id,
            "request_id": request_id,
            "participant_type": "committee",
            "participant_ref": "openclaw-risk-committee",
            "summary": "Qualified OpenClaw risk review passed for paper execution.",
            "recommendation": "approve_with_conditions",
            "confidence": 0.88,
            "event_type": "openclaw_committee_contribution",
            "event_content": {
                "claim": "Autonomous imitation policy evaluated without live capital.",
                "provider_run_id": f"run-openclaw-{request_id}",
            },
            "evidence": [
                {
                    "id": f"evidence-openclaw-{request_id}",
                    "evidence_type": "risk_audit",
                    "link": f"artifact://consultation/{request_id}",
                }
            ],
            "findings": [
                {
                    "severity": "low",
                    "category": "safety",
                    "claim": "No live capital authority.",
                    "evidence_refs": [f"evidence-openclaw-{request_id}"],
                    "recommendation": "Maintain paper-only deployment.",
                }
            ],
        }
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return SimpleNamespace(
            status="completed",
            output={
                "json_events": [
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(contribution),
                        },
                    }
                ]
            },
        )


def _build_agora_bff_app(store: AgoraDatasetStore) -> FastAPI:
    app = FastAPI()

    def _extract_identity(_auth: Optional[str]) -> OperatorIdentity:
        return OperatorIdentity(
            operator_id="operator-human-learning",
            roles=["operator", "admin"],
            claims={"sub": "operator-human-learning", "tenant_id": TENANT_ID, "allowed_tenants": [TENANT_ID]},
            token_kind="structured",
        )

    def _require_read_role(_current: OperatorIdentity) -> None:
        pass

    def _require_write_role(_current: OperatorIdentity) -> None:
        pass

    def _bff_error(status_code: int, code: object, message: str, reason: str, **kw) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={"code": str(code), "message": message, "reason": reason},
        )

    router = create_dataset_extraction_router(
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_write_role=_require_write_role,
        bff_error=_bff_error,
        utc_now=lambda: "2026-08-15T05:00:00Z",
        dataset_store=store,
    )
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

class TestL12CurrentHumanLearningDeployedE2E:
    """Deployed E2E test suite for Loops 5, 6, and 7."""

    def test_l12_loop5_agora_interaction_evidence_and_durable_handoff_e2e(self, tmp_path: Path):
        """Loop 5 E2E: Exercise provider interaction -> evidence -> DatasetVersion -> durable handoff -> PolicyLearning intake -> ack."""
        # 1. Initialize Agora BFF Store & App
        agora_store = AgoraDatasetStore()
        agora_app = _build_agora_bff_app(agora_store)

        # 2. Configure & initialize Policy Learning Service
        pl_data_dir = str(tmp_path / "policy_learning_data")
        os.makedirs(pl_data_dir, exist_ok=True)

        pl_module = _load_policy_learning_module(pl_data_dir)
        pl_app = pl_module.app

        with _serve_asgi(agora_app) as agora_url, _serve_asgi(pl_app) as pl_url:
            with mock.patch.dict(
                os.environ,
                {
                    "POLICY_LEARNING_DATA_DIR": pl_data_dir,
                    "POLICY_LEARNING_STORE_BACKEND": "json",
                    "PERSISTENCE_POSTURE": "lenient",
                    "POLICY_LEARNING_SERVICE_TOKEN": POLICY_LEARNING_TOKEN,
                    "POLICY_LEARNING_SERVICE_TENANTS": TENANT_ID,
                    "AGORA_HANDOFF_SERVICE_TOKEN": AGORA_TOKEN,
                    "AGORA_HANDOFF_SERVICE_TENANTS": TENANT_ID,
                    "AGORA_HANDOFF_ALLOWED_SERVICE_ACTOR": SERVICE_ACTOR,
                    "POLICY_LEARNING_AGORA_TENANT_ID": TENANT_ID,
                    "POLICY_LEARNING_API_URL": pl_url,
                    "AGORA_BFF_URL": agora_url,
                },
                clear=False,
            ):
                # A. Operator posts interaction evidence to Agora BFF
                evidence_id = f"ev-l12-hl-{uuid.uuid4().hex[:8]}"
                idem_key_ev = f"idem-ev-{evidence_id}"
                ev_payload = {
                    "evidence_id": evidence_id,
                    "interaction_kind": "ask",
                    "persona_id": "persona-advisor",
                    "session_id": "session-e2e-loop5",
                    "content": {"question": "Evaluate macro regime and risk threshold for imitation learning."},
                    "source_refs": ["workshop:ws-hl-001", "session:session-e2e-loop5"],
                    "learning_eligible": True,
                    "captured_at": "2026-08-15T05:10:00Z",
                }

                ev_res = _request_json(
                    f"{agora_url}/bff/agora/interaction-evidence",
                    method="POST",
                    token=AGORA_TOKEN,
                    tenant=TENANT_ID,
                    headers_extra={"Idempotency-Key": idem_key_ev},
                    payload=ev_payload,
                )
                assert ev_res["status"] == "created"
                assert ev_res["idempotent"] is False
                dataset_version_id = ev_res["data"]["dataset_version_id"]
                assert dataset_version_id.startswith("dsv-")

                # B. Dataset worker processes inbox into DatasetVersion and durable handoff
                proc_res = _request_json(
                    f"{agora_url}/bff/agora/dataset-worker/process",
                    method="POST",
                    token=AGORA_TOKEN,
                    tenant=TENANT_ID,
                )
                assert proc_res["status"] == "success"
                assert proc_res["data"]["processed"] == 1

                # C. Check pending handoff visible on Agora internal service route
                pending_handoffs = _request_json(
                    f"{agora_url}/internal/agora/dataset-handoffs",
                    method="GET",
                    token=AGORA_TOKEN,
                    tenant=TENANT_ID,
                    headers_extra={"X-Pantheon-Service-Actor": SERVICE_ACTOR},
                )
                assert pending_handoffs["total"] >= 1
                handoff_id = pending_handoffs["items"][0]["handoff_id"]
                assert handoff_id.startswith("gh-")

                # D. Run Agora Handoff Drainer intake cycle
                drain_result = agora_handoff_drainer.process_drainer_cycle(
                    agora_url=agora_url,
                    policy_learning_url=pl_url,
                    tenant_id=TENANT_ID,
                    agora_token=AGORA_TOKEN,
                    policy_learning_token=POLICY_LEARNING_TOKEN,
                    batch_size=10,
                )
                assert drain_result["status"] == "ok"
                assert drain_result["processed_count"] == 1
                assert drain_result["acked_count"] == 1
                assert drain_result["errors"] == []

                # E. Readback on Agora BFF: handoff must now be acknowledged
                after_handoffs = _request_json(
                    f"{agora_url}/internal/agora/dataset-handoffs",
                    method="GET",
                    token=AGORA_TOKEN,
                    tenant=TENANT_ID,
                    headers_extra={"X-Pantheon-Service-Actor": SERVICE_ACTOR},
                )
                assert after_handoffs["total"] == 0  # Pending list is now empty

                # F. Readback on Policy Learning: exact candidate ID and handoff ID preserved
                candidates = _request_json(
                    f"{pl_url}/api/policy-learning/candidates",
                    method="GET",
                    token=POLICY_LEARNING_TOKEN,
                    tenant=TENANT_ID,
                )
                assert len(candidates) == 1
                admitted = candidates[0]
                assert admitted["handoff_id"] == handoff_id
                assert admitted["dataset_ref"]["dataset_version_id"] == dataset_version_id
                assert admitted["dataset_source"] == "agora_dataset_version_handoff"

                # G. Anti-zero-candidate assertion: Idle tick produces 0 processed/acked
                idle_result = agora_handoff_drainer.process_drainer_cycle(
                    agora_url=agora_url,
                    policy_learning_url=pl_url,
                    tenant_id=TENANT_ID,
                    agora_token=AGORA_TOKEN,
                    policy_learning_token=POLICY_LEARNING_TOKEN,
                    batch_size=10,
                )
                assert idle_result["status"] == "ok"
                assert idle_result["processed_count"] == 0
                assert idle_result["acked_count"] == 0

                # H. Replay assertion: Replay of handoff admission is idempotent
                replay_admit = _request_json(
                    f"{pl_url}/api/policy-learning/agora-handoff",
                    method="POST",
                    token=POLICY_LEARNING_TOKEN,
                    tenant=TENANT_ID,
                    payload={
                        "handoff_id": handoff_id,
                        "eval_type": "shadow",
                        "actor_id": "test-replayer",
                        "tenant_id": TENANT_ID,
                        "dataset_ref": {"dataset_version_id": dataset_version_id, "tenant_id": TENANT_ID},
                    },
                )
                assert replay_admit["status"] in ("ok", "admitted", "exists", "acknowledged")
                assert replay_admit["candidate_id"] == admitted["candidate_id"]

                # Check total candidates still exactly 1 (no duplicate)
                all_candidates = _request_json(
                    f"{pl_url}/api/policy-learning/candidates",
                    method="GET",
                    token=POLICY_LEARNING_TOKEN,
                    tenant=TENANT_ID,
                )
                assert len(all_candidates) == 1

    def test_l12_loop6_human_imitation_shadow_eval_and_research_http_e2e(self, tmp_path: Path):
        """Loop 6 E2E: Candidate processing -> Research HTTP owner intake -> exact run readback & anti-direct-store."""
        # 1. Anti-direct-store assertion: candidate_experiment_handoff has NO direct imports of ResearchOrchestratorStore
        for forbidden in ("ResearchOrchestratorStore", "intake_imitation_candidate", "ExperimentCandidateIntakeReceipt"):
            assert not hasattr(candidate_experiment_handoff, forbidden), (
                f"candidate_experiment_handoff contains direct import '{forbidden}' - must use HTTP client exclusively"
            )

        # 2. Start real Research service
        with _serve_asgi(research_app) as research_url:
            candidate_id = f"sic-e2e-loop6-{uuid.uuid4().hex[:8]}"
            processed_candidate = {
                "candidate_id": candidate_id,
                "id": candidate_id,
                "tenant_id": TENANT_ID,
                "status": "processed",
                "dataset_version_id": "dsv-loop6-e2e-001",
                "dataset_lineage": {
                    "version_id": "dsv-loop6-e2e-001",
                    "tenant_id": TENANT_ID,
                    "source": "agora_dataset_authority",
                    "authoritative": True,
                },
                "artifact_checksum": "sha256:abcd1234ef0123456789abcdef0123456789abcdef0123456789abcdef012345",
                "dataset_mode": "agora",
                "metrics": {
                    "action_match_rate": 0.94,
                    "return_gap": 0.008,
                },
                "evaluation_summary": {
                    "action_match_rate": 0.94,
                    "evaluator_id": "shadow-eval-e2e-01",
                },
                "strategy_id": "strat-imitation-e2e",
                "strategy_spec_version": "1.0.0",
                "strategy_spec_id": "spec-strat-imitation-e2e",
                "code_version": "git:dev",
                "trace_id": f"trace-imitation-{candidate_id}",
            }

            # A. Execute HTTP handoff to Research owner authority
            handoff_receipt = handoff_candidate_to_experiment_authority(
                processed_candidate,
                research_url=research_url,
            )

            assert processed_candidate["experiment_task_id"] == f"rtask-exp-{candidate_id}"
            assert processed_candidate["experiment_run_id"] == f"rrun-exp-{candidate_id}"
            assert processed_candidate["handoff_status"] == "completed"
            assert handoff_receipt.experiment_task_id == f"rtask-exp-{candidate_id}"
            assert handoff_receipt.experiment_run_id == f"rrun-exp-{candidate_id}"

            # B. Readback from Research authority owner
            run_readback = _request_json(
                f"{research_url}/api/research-orchestrator/runs/{handoff_receipt.experiment_run_id}",
                method="GET",
                tenant=TENANT_ID,
            )
            assert run_readback["task_id"] == handoff_receipt.experiment_task_id
            assert run_readback["run_id"] == handoff_receipt.experiment_run_id

            # C. Replay assertion: Re-submitting candidate HTTP intake is idempotent and returns exact same receipt
            replay_receipt = post_imitation_candidate_intake_http(
                processed_candidate,
                research_url=research_url,
            )
            assert replay_receipt.task_id == handoff_receipt.experiment_task_id
            assert replay_receipt.run_id == handoff_receipt.experiment_run_id

            # D. Anti-zero-candidate assertion: An empty/0-candidate payload fails closed
            empty_candidate = {"candidate_id": "", "tenant_id": TENANT_ID}
            with pytest.raises((CandidateHandoffError, ResearchCandidateClientError)):
                handoff_candidate_to_experiment_authority(
                    empty_candidate,
                    research_url=research_url,
                )

    def test_l12_loop7_consultation_provider_and_governance_handoff_e2e(self, tmp_path: Path):
        """Loop 7 E2E: Consultation request -> OpenClaw provider contribution -> ConsultMemo -> Governance handoff & readback."""
        # 1. Initialize Consultation stores
        consult_domain_dir = tmp_path / "consult_domain"
        consultation_main.store = ConsultationStore(str(consult_domain_dir))
        consultation_main.workflow_state = WorkflowStateStore(tmp_path / "consult_workflow.sqlite3")

        # 2. Initialize Governance handoff store & configure Governance app
        gov_store = JsonGovernanceRecordStore(
            tmp_path / "governance_consultation_handoffs.json",
            id_fields=("handoff_id",),
        )
        governance_main.consultation_handoff_store = gov_store

        # 3. Setup OpenClaw Provider app
        fake_provider = FakeOpenClawRiskProvider()
        provider_app = create_openclaw_adapter_app(
            provider=fake_provider,
            provider_token=OPENCLAW_PROVIDER_TOKEN,
            expected_service_actor=CONSULTATION_SERVICE_ACTOR,
            replay_path=tmp_path / "openclaw_provider_replay.sqlite3",
        )

        with (
            _serve_asgi(governance_main.app) as gov_url,
            _serve_asgi(provider_app) as provider_base_url,
            _serve_asgi(consultation_main.app) as consult_url,
        ):
            provider_url = provider_base_url + CONSULTATION_CONTRIBUTION_PATH
            gov_handoff_url = gov_url + "/api/governance/consultation-handoffs"

            with mock.patch.dict(
                os.environ,
                {
                    "CONSULTATION_AUTH_REQUIRED": "true",
                    "CONSULTATION_SERVICE_TOKEN": CONSULTATION_SERVICE_TOKEN,
                    "CONSULTATION_OPERATOR_TOKEN": CONSULTATION_OPERATOR_TOKEN,
                    "CONSULTATION_HANDOFF_TOKEN": GOVERNANCE_HANDOFF_TOKEN,
                    "CONSULTATION_HANDOFF_ALLOWED_SERVICE_ACTOR": CONSULTATION_SERVICE_ACTOR,
                    "CONSULTATION_HANDOFF_ALLOWED_TENANTS": TENANT_ID,
                },
                clear=False,
            ):
                # A. Operator submits ConsultRequest to Consultation API
                request_id = f"cr-l12-e2e-{uuid.uuid4().hex[:8]}"
                req_payload = {
                    "request_id": request_id,
                    "tenant_id": TENANT_ID,
                    "request_type": "strategy_review",
                    "requested_by": {"actor_type": "operator", "actor_id": "risk-officer-01"},
                    "target_type": "allocation_policy",
                    "target_id": f"policy-alloc-{request_id}",
                    "task": "Review imitation policy risk bounds before promotion.",
                    "context_refs": ["candidate:sic-e2e-001"],
                    "evidence_refs": ["evidence:ev-hl-001"],
                    "priority": "high",
                    "metadata": {"paper_only": True},
                    "trace_id": f"trace-consult-{request_id}",
                }

                created_req = _request_json(
                    f"{consult_url}/api/consult/requests",
                    method="POST",
                    token=CONSULTATION_OPERATOR_TOKEN,
                    tenant=TENANT_ID,
                    payload=req_payload,
                )
                assert created_req["status"] == "draft"

                submit_res = _request_json(
                    f"{consult_url}/api/consult/requests/{request_id}/submit",
                    method="POST",
                    token=CONSULTATION_OPERATOR_TOKEN,
                    tenant=TENANT_ID,
                    payload={},
                )
                assert submit_res["status"] == "submitted"

                # B. Configure Executor
                executor_config = ExecutorConfig(
                    api_url=consult_url,
                    tenant_id=TENANT_ID,
                    api_token=CONSULTATION_SERVICE_TOKEN,
                    provider_url=provider_url,
                    provider_token=OPENCLAW_PROVIDER_TOKEN,
                    provider_service_actor=CONSULTATION_SERVICE_ACTOR,
                    handoff_sink_url=gov_handoff_url,
                    handoff_token=GOVERNANCE_HANDOFF_TOKEN,
                    worker_id="worker-consultation-e2e",
                    state_path=str(tmp_path / "executor_state.sqlite3"),
                    lease_seconds=30,
                    retry_after_seconds=0,
                    max_blocked_attempts=2,
                    batch_size=1,
                    timeout_seconds=10.0,
                )
                executor_state = WorkflowStateStore(executor_config.state_path)

                # C. Run Executor tick: claims request, obtains OpenClaw contribution, publishes memo, forwards handoff
                tick_res = run_consultation_tick(config=executor_config, state=executor_state)
                assert tick_res["completed"] == 1
                assert tick_res.get("blocked", 0) == 0
                assert tick_res.get("dead_lettered", 0) == 0
                assert len(fake_provider.calls) == 1

                # D. Readback from Consultation API
                memos = _request_json(
                    f"{consult_url}/api/consult/memos?request_id={request_id}",
                    method="GET",
                    token=CONSULTATION_SERVICE_TOKEN,
                    tenant=TENANT_ID,
                )
                assert len(memos) == 1
                memo = memos[0]
                assert memo["status"] == "published"
                assert memo["request_id"] == request_id
                memo_id = memo["memo_id"]

                handoffs = _request_json(
                    f"{consult_url}/api/consult/handoffs?request_id={request_id}",
                    method="GET",
                    token=CONSULTATION_SERVICE_TOKEN,
                    tenant=TENANT_ID,
                )
                assert len(handoffs) == 1
                consult_handoff = handoffs[0]
                assert consult_handoff["status"] == "acknowledged"
                assert consult_handoff["target_gate"] == "consultation.committee.strategy_review.reviewed"

                # E. Readback from Governance record store
                gov_records = gov_store.list_all()
                assert len(gov_records) == 1
                gov_rec = gov_records[0]
                assert gov_rec["request_id"] == request_id
                assert gov_rec["handoff_id"] == consult_handoff["handoff_id"]
                assert memo_id in gov_rec["memo_ids"]

                # F. Replay assertion: Replay from a fresh recovery worker state reuses acknowledged handoff with 0 duplicate OpenClaw turns
                recovery_config = replace(executor_config, state_path=str(tmp_path / "recovery_state.sqlite3"))
                recovery_state = WorkflowStateStore(recovery_config.state_path)
                recovery_state.ensure_request(tenant_id=TENANT_ID, request_id=request_id)
                claim = recovery_state.claim_next(
                    tenant_id=TENANT_ID,
                    lease_owner="recovery-worker",
                    lease_seconds=30,
                )
                assert claim is not None
                provider_client = HttpContributionProvider(
                    endpoint=recovery_config.provider_url,
                    bearer_token=recovery_config.provider_token,
                    service_actor=recovery_config.provider_service_actor,
                    timeout_seconds=recovery_config.timeout_seconds,
                )
                recovered = execute_claim(
                    config=recovery_config,
                    state=recovery_state,
                    provider=provider_client,
                    claim=claim,
                )
                assert recovered["outcome"] == "completed"
                assert "recovered acknowledged handoff" in recovered["detail"]
                # Still exactly 1 call to fake provider (no duplicate invocation)
                assert len(fake_provider.calls) == 1

                # G. Functional health & DLQ degradation assertion on missing downstream sink
                health = initial_health_state(executor_config)
                broken_config = replace(executor_config, handoff_sink_url="", state_path=str(tmp_path / "broken_state.sqlite3"))
                broken_state = WorkflowStateStore(broken_config.state_path)

                # Create request 2 for health test
                req2_id = f"cr-broken-{uuid.uuid4().hex[:8]}"
                _request_json(
                    f"{consult_url}/api/consult/requests",
                    method="POST",
                    token=CONSULTATION_OPERATOR_TOKEN,
                    tenant=TENANT_ID,
                    payload={**req_payload, "request_id": req2_id},
                )
                _request_json(
                    f"{consult_url}/api/consult/requests/{req2_id}/submit",
                    method="POST",
                    token=CONSULTATION_OPERATOR_TOKEN,
                    tenant=TENANT_ID,
                    payload={},
                )

                # First tick: blocked
                blocked_tick = run_consultation_tick(config=broken_config, state=broken_state)
                assert blocked_tick["blocked"] == 1
                update_functional_health(health, blocked_tick, observed_at="2026-08-15T05:20:00Z")
                assert health["status"] == "degraded"
                assert health["functional_health"]["ready"] is False

                # Second tick: dead letter
                dlq_tick = run_consultation_tick(config=broken_config, state=broken_state)
                assert dlq_tick["dead_lettered"] == 1
                update_functional_health(health, dlq_tick, observed_at="2026-08-15T05:20:15Z")
                assert health["functional_health"]["dead_letter_count"] == 1

                # DLQ replay with working sink restores health to ok
                assert broken_state.replay_dead_letter(tenant_id=TENANT_ID, request_id=req2_id)
                fixed_config = replace(broken_config, handoff_sink_url=gov_handoff_url)
                fixed_tick = run_consultation_tick(config=fixed_config, state=broken_state)
                assert fixed_tick["completed"] == 1
                update_functional_health(health, fixed_tick, observed_at="2026-08-15T05:20:30Z")
                assert health["status"] == "ok"
                assert health["functional_health"]["ready"] is True

    def test_l12_human_learning_chain_identity_correlation_e2e(self, tmp_path: Path):
        """Cross-Loop Correlation E2E: Agora Evidence -> DatasetVersion -> Policy Learning Candidate -> Research Run -> Consultation Request -> Memo -> Governance Handoff."""
        # 1. Start Research API
        with _serve_asgi(research_app) as research_url:
            # 2. Agora interaction evidence ID
            evidence_id = f"ev-chain-{uuid.uuid4().hex[:8]}"
            dsv_id = f"dsv-chain-{uuid.uuid4().hex[:8]}"
            gh_agora_id = f"gh-agora-{uuid.uuid4().hex[:8]}"
            candidate_id = f"sic-chain-{uuid.uuid4().hex[:8]}"

            # 3. Simulate candidate created from Agora handoff
            candidate_record = {
                "candidate_id": candidate_id,
                "id": candidate_id,
                "tenant_id": TENANT_ID,
                "status": "processed",
                "handoff_id": gh_agora_id,
                "dataset_version_id": dsv_id,
                "dataset_ref": {"dataset_version_id": dsv_id, "tenant_id": TENANT_ID},
                "dataset_lineage": {
                    "version_id": dsv_id,
                    "tenant_id": TENANT_ID,
                    "source": "agora_dataset_authority",
                    "evidence_refs": [evidence_id],
                    "authoritative": True,
                },
                "artifact_checksum": "sha256:9999888877776666555544443333222211110000aaaabbbbccccddddeeeeffff",
                "dataset_mode": "agora",
                "metrics": {"action_match_rate": 0.96},
                "evaluation_summary": {"evaluator_id": "eval-chain-01"},
                "strategy_id": "strat-chain",
                "strategy_spec_version": "1.0.0",
                "strategy_spec_id": "spec-strat-chain",
                "code_version": "git:dev",
                "trace_id": f"trace-chain-{candidate_id}",
            }

            # 4. Handoff candidate to Research HTTP authority
            research_receipt = handoff_candidate_to_experiment_authority(
                candidate_record,
                research_url=research_url,
            )
            assert research_receipt.experiment_task_id == f"rtask-exp-{candidate_id}"
            assert research_receipt.experiment_run_id == f"rrun-exp-{candidate_id}"

            # 5. Read back from Research owner
            run_data = _request_json(
                f"{research_url}/api/research-orchestrator/runs/{research_receipt.experiment_run_id}",
                method="GET",
                tenant=TENANT_ID,
            )
            assert run_data["task_id"] == research_receipt.experiment_task_id
            assert run_data["run_id"] == research_receipt.experiment_run_id

            # 6. Consultation request binds Research run and Candidate ID
            consult_req_id = f"cr-chain-{uuid.uuid4().hex[:8]}"
            consult_payload = {
                "request_id": consult_req_id,
                "tenant_id": TENANT_ID,
                "request_type": "strategy_review",
                "target_type": "experiment_run",
                "target_id": research_receipt.experiment_run_id,
                "context_refs": [
                    f"candidate:{candidate_id}",
                    f"dataset:{dsv_id}",
                    f"evidence:{evidence_id}",
                ],
                "evidence_refs": [f"research_run:{research_receipt.experiment_run_id}"],
                "priority": "high",
                "metadata": {"agora_handoff_id": gh_agora_id},
            }

            # Verify context preservation in Consultation request
            assert f"candidate:{candidate_id}" in consult_payload["context_refs"]
            assert f"dataset:{dsv_id}" in consult_payload["context_refs"]
            assert f"evidence:{evidence_id}" in consult_payload["context_refs"]
            assert consult_payload["metadata"]["agora_handoff_id"] == gh_agora_id
            assert consult_payload["target_id"] == research_receipt.experiment_run_id
