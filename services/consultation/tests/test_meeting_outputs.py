"""Integration and unit tests for meeting proposal output routing (MEETING-OUTPUT-ROUTING-20261115).

Dispatches one proposal of each of the eight governed kinds to the real owner
routes (in-process FastAPI apps) and reads back live status, proving:
  1. Single idempotent dispatch under the sponsor persona's own identity;
     advisors and private servants never sponsor; ``sponsor_persona_id`` is
     ``None`` only for ``agora_candidate_decision``.
  2. Downstream gates are unchanged: every dispatched proposal enters at its
     entry point's first state and nothing is marked approved or applied.
  3. Dispatch failures are recorded on the proposal and retried idempotently;
     the meeting record carries each proposal's downstream id and live status
     read from the owner.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping
import urllib.parse
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import pytest

_TMP_ROOT = tempfile.mkdtemp(prefix="meeting_outputs_test_")
os.environ.setdefault("GOVERNANCE_DATA_DIR", str(Path(_TMP_ROOT) / "gov"))
os.environ.setdefault("EVOLUTION_DATA_DIR", str(Path(_TMP_ROOT) / "evo"))
os.environ.setdefault("INCIDENT_DATA_DIR", str(Path(_TMP_ROOT) / "inc"))
os.environ.setdefault("PANTHEON_MEMORY_DATA_DIR", str(Path(_TMP_ROOT) / "mem"))

from services.consultation.meeting_outputs import (  # noqa: E402
    FORBIDDEN_APPROVED_OR_APPLIED_STATES,
    SUPPORTED_PROPOSAL_KINDS,
    dispatch_proposals,
    list_dispatched_proposals,
    output_routing_context,
    read_downstream_status,
)
from services.foundation.reliable_delivery import AtomicJsonRecordStore  # noqa: E402
from services.governance import main as governance_main  # noqa: E402
from services.governance.pg_store import (  # noqa: E402
    ApprovalCommandConflict,
    ApprovalCommandNotFound,
    JsonlGovernanceAuditStore,
)
from services.knowledge import case_file as cf  # noqa: E402
from services.memory import main as memory_main  # noqa: E402
from services.persona.harness import HarnessSurface, PersistentHarnessOwner  # noqa: E402
from services.persona.lesson_governance import (  # noqa: E402
    LessonGovernanceService,
    TradeLessonCandidateStore,
    utc_now,
)
from services.persona.lesson_rule_proposals import LessonRuleConverter  # noqa: E402
from services.persona.test_harness import BASE as HARNESS_BASE_SURFACE  # noqa: E402
from services.persona.write_owner import (  # noqa: E402
    CreatePersonaRequest,
    PersonaAuthorityError,
    PersistentPersonaOwner,
    _AUTHENTICATED_MUTATION_ROLES,
    create_app as create_persona_app,
    resolve_persona_tenant_scope,
)
from services.research.tests.test_research_orchestrator_http_service import (  # noqa: E402
    _load_service_module as load_research_module,
)
from services.runtime_auth_inbound import AuthError, validate_request_auth  # noqa: E402
from services.telemetry import main as telemetry_main  # noqa: E402
from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore  # noqa: E402

from services.evolution import main as evolution_main  # noqa: E402
sys.modules.pop("models", None)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSONA_DIR = _REPO_ROOT / "services" / "persona"
if str(_PERSONA_DIR) not in sys.path:
    sys.path.insert(0, str(_PERSONA_DIR))
_BFF_ROOT = _REPO_ROOT / "services" / "control-plane" / "bff"
if str(_BFF_ROOT) not in sys.path:
    sys.path.insert(0, str(_BFF_ROOT))
_CP_PERSONA = _REPO_ROOT / "services" / "control-plane" / "persona"
if str(_CP_PERSONA) not in sys.path:
    sys.path.insert(0, str(_CP_PERSONA))
_CP_GOV = _REPO_ROOT / "services" / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from agora.candidate_decisions.models import CandidateDecisionCommand  # noqa: E402
from agora.candidate_decisions.router import create_candidate_decision_router  # noqa: E402
from agora.candidate_decisions.service import CandidateDecisionService  # noqa: E402
from agora.candidate_decisions.store import CandidateDecisionStore  # noqa: E402
from agora.interaction.provider import (  # noqa: E402
    RecommendedMeasure,
    authority_boundary,
    recommended_measure_sha256,
)
from agora.interaction.store import InteractionLifecycleStore  # noqa: E402
from approval_decision import ApprovalDecision, ApprovalDecisionStore  # noqa: E402
from evolution_decision import EvolutionDecisionStore  # noqa: E402
from models import OperatorIdentity  # noqa: E402
from persona_strategy_pool import (  # noqa: E402
    AppendPoolVersionRequest,
    PersonaStrategyPoolStore,
    PoolDecisionRequest,
    PoolError,
    PoolMember,
)

SECRET = "meeting-output-routing-test-secret-0123456789"
ISSUER = "pantheon-test-issuer"
AUDIENCE = "pantheon-test-audience"
TENANT = "tenant-a"
SPONSOR = "persona-alpha"
MEETING_ID = "mtg-20261116-001"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


class _TransactionalJsonApprovalDecisionStore(ApprovalDecisionStore):
    """In-process ApprovalDecisionStore with the exact execute_command CAS/receipt contract."""

    def __init__(self, storage_path: str) -> None:
        super().__init__(storage_path=storage_path)
        self._receipts: dict[str, dict[str, Any]] = {}

    def execute_command(self, *, command: dict[str, Any], mutate: Any, audit_store: Any) -> dict[str, Any]:
        encoded = json.dumps(command, sort_keys=True, separators=(",", ":"))
        request_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        scope = [command["tenant_id"], command["actor_id"], command["idempotency_key"]]
        receipt_id = hashlib.sha256(json.dumps(scope).encode("utf-8")).hexdigest()
        existing_receipt = self._receipts.get(receipt_id)
        if existing_receipt is not None:
            if existing_receipt.get("request_digest") != request_digest:
                raise ApprovalCommandConflict("Idempotency key has different command content")
            return copy.deepcopy(existing_receipt["response"])

        base = self.get(command["decision_id"])
        if (base is None and command["operation"] != "propose") or (
            base is not None and base.tenant_id != command["tenant_id"]
        ):
            raise ApprovalCommandNotFound("Approval decision not found")
        if (base.version if base else 0) != command["expected_version"]:
            raise ApprovalCommandConflict("Approval base version is stale")

        decision = mutate(ApprovalDecision.from_dict(base.to_dict()) if base else None)
        decision.version = command["expected_version"] + 1
        decision.event_id = str(uuid.uuid4())
        self.put(decision)
        payload = decision.to_dict()
        audit_store.append_event(
            event_type="approval_decision_created" if command["operation"] == "propose" else "approval_decision_state_changed",
            decision_id=decision.decision_id,
            actor_id=command["actor_id"],
            actor_role=command["actor_role"],
            target_type=str(decision.target_type.value if hasattr(decision.target_type, "value") else decision.target_type),
            target_id=decision.target_id,
            detail={"version": decision.version, "request_digest": request_digest},
        )
        self._receipts[receipt_id] = {
            "request_digest": request_digest,
            "command": copy.deepcopy(command),
            "response": copy.deepcopy(payload),
        }
        return payload


class _ValidationAdapter:
    adapter_id = "validator-prod"

    def readiness(self, *, candidate: Mapping[str, Any]) -> dict[str, Any]:
        return {"ready": True, "reason": None}

    def validate(self, request: Any, *, validation_plan: Any, candidate: Any) -> dict[str, Any]:
        raise NotImplementedError


class _ApprovalStoreStub:
    def readiness(self) -> dict[str, Any]:
        return {"ready": True, "reason": None}

    def get_formal_approval(self, approval_decision_id: str) -> dict[str, Any] | None:
        return None


def _make_case_file(lesson_ids: list[str], persona: str = SPONSOR, tenant: str = TENANT) -> dict[str, Any]:
    items = [
        {
            "item_id": f"lessons:persona:pmem-lesson-{lid}",
            "group": "lessons",
            "kind": "persona",
            "source_owner": "memory",
            "source_id": f"pmem-lesson-{lid}",
            "excerpt": "endorsed lesson excerpt",
            "retrieval_score": 0.92,
            "retrieval_mode": "hybrid",
            "subject_fields": {},
        }
        for lid in lesson_ids
    ]
    empty = {"status": "empty", "items": [], "truncated": False, "unavailable": []}
    body = {
        "schema_version": "decision_case_file.v1",
        "composed_at": "2026-11-16T00:00:00+00:00",
        "subject": {
            "decision_type": "consult",
            "persona_id": persona,
            "tenant_id": tenant,
            "strategy_family": "momentum",
            "underlyings": [],
            "instrument_class": "",
            "member_artifact_refs": [],
            "backtest_run_refs": [],
            "regime_tag": "",
        },
        "caller": {"actor_id": persona, "is_subject_persona": True},
        "lesson_query": {"query": "momentum", "tags": []},
        "budgets": {"max_items_per_group": 8, "max_excerpt_chars": 400, "max_total_chars": 12000},
        "groups": {
            "facts": copy.deepcopy(empty),
            "similar_history": copy.deepcopy(empty),
            "lessons": {"status": "ok", "items": items, "truncated": False, "unavailable": []},
            "evidence": copy.deepcopy(empty),
        },
        "execution_may_read": False,
    }
    digest = cf._digest(body)
    return {"case_file_id": f"casefile-{digest[:24]}", "digest": digest, **body}


def _seed_agora_interaction(interactions: InteractionLifecycleStore) -> None:
    measure_raw = {
        "measure_id": "measure-agora-1",
        "measure_type": "risk_limit_recommendation",
        "target": {
            "kind": "strategy",
            "id": "strategy-1",
            "version": "version-1",
            "path": "risk.max_position",
        },
        "current_value": 0.10,
        "proposed_value": 0.07,
        "rationale": "Reduce single-position risk limit during elevated volatility.",
        "expected_benefit": "Lower paper drawdown tail.",
        "adverse_scenarios": ["Tighter cap may clip upside in strong trends."],
        "confidence": 0.78,
        "evidence_refs": [
            {
                "ref_type": "journal_entry",
                "ref_id": "journal-101",
                "version": "v1",
                "observed_at": NOW.isoformat(),
                "data_cutoff": NOW.isoformat(),
                "freshness": "fresh",
                "summary": "Observed volatility spike across three sessions.",
            }
        ],
        "environment_ceiling": "paper",
        "validation_plan": {
            "validator": "validator-prod",
            "required_checks": ["source_binding", "evidence_freshness", "target_version"],
        },
        "rollback_trigger": "Paper drawdown does not improve.",
        "rollback_action": "Restore prior position cap.",
        "authority": authority_boundary(),
    }
    measure = RecommendedMeasure.model_validate(measure_raw).model_dump(mode="json")
    measure["measure_sha256"] = recommended_measure_sha256(measure)
    opinion = {
        "opinion_id": "opinion-agora-1",
        "interaction_id": MEETING_ID,
        "participant": {"persona_id": "persona-risk-officer", "persona_version": "v1"},
        "provider_invocation_id": "inv-openclaw-1",
        "conclusion": "conditional",
        "rationale": "Recommend lower paper position cap.",
        "recommended_measures": [measure],
        "authority": authority_boundary(),
        "provenance": {
            "content_origin": "selected_persona_provider_response",
            "provider_kind": "openclaw",
            "provider_invocation_id": "inv-openclaw-1",
            "request_correlated": True,
            "response_correlated": True,
            "canned_template": False,
            "magic_topic_trigger": False,
            "simulation": False,
        },
    }
    persisted = {
        "interaction_id": MEETING_ID,
        "tenant_id": TENANT,
        "owner_user_id": "user-a",
        "workshop_id": "workshop-a",
        "status": "completed",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "human_request": {"request_text": "Review strategy risk"},
        "context_snapshot": {"tenant_id": TENANT},
        "opinions": [opinion],
    }
    interactions._requests[MEETING_ID] = persisted
    interactions._invocations[MEETING_ID] = {
        "inv-openclaw-1": {
            "invocation": {
                "invocation_id": "inv-openclaw-1",
                "participant": opinion["participant"],
                "status": "succeeded",
            },
            "opinion": opinion,
            "error": None,
            "status": "succeeded",
        }
    }


def _seed_telemetry_episode(
    store: TradeEpisodeProjectionStore,
    episode_id: str,
    *,
    tenant_id: str = TENANT,
    persona_id: str = SPONSOR,
    regime: str = "bull",
    realized_pnl: float = 125.0,
) -> dict[str, Any]:
    store.project_event(
        {
            "event_id": f"ev-open-{episode_id}",
            "event_type": "trade_episode.opened",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "environment": "paper",
            "persona_id": persona_id,
            "strategy_id": "strat-member-1",
            "artifact_id": "strat-member-1",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-paper-1",
            "instrument_id": "2330.TW",
            "occurred_at": "2026-11-10T01:00:00Z",
            "sequence_number": 1,
            "payload": {
                "side": "long",
                "status": "open",
                "opened_at": "2026-11-10T01:00:00Z",
                "entry_actor": "persona",
                "requested_quantity": 100.0,
                "filled_quantity": 100.0,
                "remaining_quantity": 0.0,
                "vwap": 580.0,
                "fees": 12.5,
                "confidence": 0.84,
                "thesis": f"Paper momentum trade in {regime} regime",
            },
        }
    )
    projected = store.project_event(
        {
            "event_id": f"ev-close-{episode_id}",
            "event_type": "trade_episode.closed",
            "trade_episode_id": episode_id,
            "tenant_id": tenant_id,
            "environment": "paper",
            "persona_id": persona_id,
            "occurred_at": "2026-11-11T05:30:00Z",
            "sequence_number": 2,
            "payload": {
                "status": "closed",
                "closed_at": "2026-11-11T05:30:00Z",
                "exit_actor": "persona",
                "exit_reason": "target_reached",
                "realized_pnl": realized_pnl,
            },
        }
    )
    assert projected is not None
    return projected


@pytest.fixture
def owner_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mount all 7 owner services (covering all 8 proposal kinds + Telemetry episodes) in-process via TestClient."""
    # Common auth configuration
    for prefix in (
        "PANTHEON_RESEARCH_",
        "PANTHEON_MEMORY_",
        "PANTHEON_TELEMETRY_",
        "PANTHEON_GOVERNANCE_",
        "PERSONA_",
        "PANTHEON_BFF_",
    ):
        monkeypatch.setenv(prefix + "AUTH_MODE", "strict")
        monkeypatch.setenv(prefix + "JWT_SECRET", SECRET)
        monkeypatch.setenv(prefix + "JWT_ISSUER", ISSUER)
        monkeypatch.setenv(prefix + "JWT_AUDIENCE", AUDIENCE)
        monkeypatch.setenv(prefix + "MFA_REQUIRED", "false")
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "disabled")
    monkeypatch.setenv("EVOLUTION_DEFAULT_TENANT_ID", TENANT)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", TENANT)

    # 1. Research owner app
    research_module = load_research_module(data_dir=str(tmp_path / "research"))
    monkeypatch.syspath_prepend(str(_CP_GOV))
    monkeypatch.syspath_prepend(str(_CP_PERSONA))
    monkeypatch.syspath_prepend(str(_BFF_ROOT))
    research_client = TestClient(research_module.app)

    # 2. Evolution owner app
    evo_dir = tmp_path / "evolution"
    evo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(evolution_main, "store", EvolutionDecisionStore(storage_path=str(evo_dir / "decisions.json")))
    monkeypatch.setattr(
        evolution_main,
        "proposal_inbox",
        evolution_main._EvolutionProposalInbox(evo_dir / "inbox.json"),
    )
    evolution_client = TestClient(evolution_main.app)

    # 3. Governance owner app
    gov_dir = tmp_path / "governance"
    gov_dir.mkdir(parents=True, exist_ok=True)
    gov_store = _TransactionalJsonApprovalDecisionStore(str(gov_dir / "approvals.json"))
    gov_audit = JsonlGovernanceAuditStore(str(gov_dir / "audit.jsonl"))
    monkeypatch.setattr(governance_main, "store", gov_store)
    monkeypatch.setattr(governance_main, "audit_store", gov_audit)
    governance_client = TestClient(governance_main.app)

    # 4. Memory owner app (trade-lessons)
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PANTHEON_MEMORY_DATA_DIR", str(mem_dir))
    lesson_store = TradeLessonCandidateStore(path=mem_dir / "trade_lesson_candidates.json")
    lesson_gov_service = LessonGovernanceService(lesson_store)
    monkeypatch.setattr(memory_main, "_candidate_store", lambda: lesson_store)
    monkeypatch.setattr(memory_main, "_governance_service", lambda: lesson_gov_service)
    memory_client = TestClient(memory_main.app)

    # Seed one endorsed lesson in Memory so lesson_rule conversion can validate it
    endorsed_lesson_id = str(uuid.uuid4())
    seed_lesson = {
        "lesson_candidate_id": endorsed_lesson_id,
        "reflection_id": str(uuid.uuid4()),
        "trade_episode_ids": [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())],
        "persona_id": SPONSOR,
        "tenant_id": TENANT,
        "scope": "strategy",
        "proposed_change": "Require dual regime confirmation before entry",
        "confidence": 0.85,
        "review_state": "proposed",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "expiry": "2099-01-01T00:00:00Z",
        "reflection_version": "v1",
    }
    lesson_gov_service.store.create(seed_lesson)
    episodes = [
        {"trade_episode_id": ep, "tenant_id": TENANT, "persona_id": SPONSOR, "regime": reg}
        for ep, reg in zip(seed_lesson["trade_episode_ids"], ("bull", "bear", "bull"))
    ]
    lesson_gov_service.decide(
        endorsed_lesson_id,
        action="endorse",
        operator_id="op-reviewer",
        reason="passes sample and regime gates",
        audit_receipt_id=str(uuid.uuid4()),
        episodes=episodes,
    )

    # 5. Persona owner app (harness, lesson-rule-proposals, strategy-pool)
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir(parents=True, exist_ok=True)
    persona_owner = PersistentPersonaOwner.from_json_path(persona_dir / "personas.json")
    persona_owner.create(
        CreatePersonaRequest(
            actor_id=SPONSOR,
            persona_id=SPONSOR,
            name="Alpha Momentum",
            mandate="Paper momentum trading",
            tenant_id=TENANT,
        )
    )
    raw_persona = persona_owner._records.get(SPONSOR)
    assert persona_owner._records.compare_and_set(
        SPONSOR, raw_persona, {**raw_persona, "lifecycle_state": "paper_owner", "status": "active"}
    )[0]
    harness_owner = PersistentHarnessOwner(
        persona_owner,
        evidence_verifier=lambda _persona, _ref: True,
    )
    harness_owner.initialize(SPONSOR, HarnessSurface.model_validate(HARNESS_BASE_SURFACE), SPONSOR)

    class _DirectLessonReader:
        def get(self, lesson_id: str, tenant_id: str, authorization: str | None) -> dict[str, Any] | None:
            return lesson_store.get(lesson_id)

    def _in_process_gov_sender(payload: dict[str, Any]) -> dict[str, Any]:
        from services.consultation.meeting_outputs import _auth_headers

        hdrs = _auth_headers(
            kind="governance_approval",
            tenant_id=payload["tenant_id"],
            sponsor_persona_id=payload["owner_user_id"],
            idempotency_key=payload["decision_id"],
        )
        resp = governance_client.post("/api/governance/approvals", headers=hdrs, json=payload)
        assert resp.status_code in (200, 201), resp.text
        return resp.json()

    converter = LessonRuleConverter(
        AtomicJsonRecordStore(persona_dir / "lesson_rule_proposals.json"),
        harness_owner,
        reader=_DirectLessonReader(),
        governance_sender=_in_process_gov_sender,
        proposer_id=SPONSOR,
    )
    persona_app = create_persona_app(
        owner=persona_owner,
        harness_owner=harness_owner,
        lesson_rule_converter=converter,
    )

    # Mount the real strategy-pool routes on persona_app
    pool_store = PersonaStrategyPoolStore(
        records=AtomicJsonRecordStore(persona_dir / "pools.json"),
        registry_reader=lambda ref, _tenant: {
            "registry_id": ref,
            "owner_tenant": TENANT,
            "artifact_type": "strategy_spec",
            "artifact_state": "approved",
            "checksum": "sha256:" + "a" * 64,
            "approval_decision_id": "apv-seed-1",
            "metadata": {
                "persona_id": SPONSOR,
                "strategy_artifact": {"parameters": {"symbols": ["2330.TW"]}},
            },
        },
        now=lambda: NOW.isoformat(),
    )
    pool_store.append(
        SPONSOR,
        TENANT,
        AppendPoolVersionRequest(
            expected_version=0,
            members=[
                PoolMember(
                    artifact_ref="strat-member-1",
                    checksum="sha256:" + "a" * 64,
                    stage="active",
                    risk_weight=0.4,
                    baseline_ref="apv-seed-1",
                    joined_at="2026-10-01T00:00:00Z",
                )
            ],
            change_reason="bootstrap pool v1",
        ),
        actor_id=SPONSOR,
    )

    def _pool_scope(persona_id: str, authorization: str | None, tenant_id: str | None, *, write: bool):
        try:
            authority, admitted = resolve_persona_tenant_scope(
                authorization, tenant_id, *((_AUTHENTICATED_MUTATION_ROLES,) if write else ())
            )
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
        rec = persona_owner._records.get(persona_id)
        if rec is None or rec.get("tenant_id") != admitted:
            raise HTTPException(status_code=403 if write else 404, detail="persona scope denied")
        return authority, admitted

    @persona_app.post("/api/personas/{persona_id}/strategy-pool/decisions", status_code=201)
    def decide_strategy_pool(
        persona_id: str,
        body: PoolDecisionRequest,
        tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
        authorization: str | None = Header(default=None),
    ):
        authority, admitted = _pool_scope(persona_id, authorization, tenant_id, write=True)
        try:
            return pool_store.decide(persona_id, admitted, body, actor_id=authority.actor_id)
        except PoolError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @persona_app.get("/api/personas/{persona_id}/strategy-pool/versions/{pool_version}")
    def get_strategy_pool_version(
        persona_id: str,
        pool_version: int,
        tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
        authorization: str | None = Header(default=None),
    ):
        _, admitted = _pool_scope(persona_id, authorization, tenant_id, write=False)
        return pool_store.version(persona_id, admitted, pool_version)

    persona_client = TestClient(persona_app)

    # 6. Agora candidate-decision owner app
    agora_interactions = InteractionLifecycleStore(backend="memory")
    _seed_agora_interaction(agora_interactions)
    agora_service = CandidateDecisionService(
        CandidateDecisionStore(backend="off"),
        interaction_store=agora_interactions,
        validation_adapter=_ValidationAdapter(),
        approval_store=_ApprovalStoreStub(),
        clock=lambda: NOW,
    )
    agora_identity = OperatorIdentity(
        operator_id="operator-a",
        roles=["operator"],
        claims={"tenant_id": TENANT, "sub": "user-a"},
    )
    agora_app = FastAPI()
    agora_app.include_router(
        create_candidate_decision_router(
            service=agora_service,
            extract_identity=lambda _auth: agora_identity,
            require_read_role=lambda _identity: None,
            require_write_role=lambda _identity: None,
            bff_error=lambda *args, **_kwargs: HTTPException(args[0], detail=str(args[2])),
            utc_now=lambda: NOW.isoformat(),
        )
    )
    agora_client = TestClient(agora_app)

    # 7. Real Telemetry Flask owner app (services.telemetry.main.app)
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    telemetry_store = TradeEpisodeProjectionStore(
        projections_path=telemetry_dir / "trade_episode_projections.json",
        events_path=telemetry_dir / "trade_episode_events.jsonl",
    )
    for ep_id, reg in zip(seed_lesson["trade_episode_ids"], ("bull", "bear", "bull")):
        _seed_telemetry_episode(telemetry_store, ep_id, regime=reg)
    for ep_id, reg in (
        ("ep-101", "bull"),
        ("ep-102", "bear"),
        ("ep-103", "bull"),
        ("ep-201", "bull"),
        ("ep-202", "bear"),
        ("ep-203", "bull"),
        ("ep-301", "bull"),
        ("ep-302", "bear"),
        ("ep-303", "bull"),
    ):
        _seed_telemetry_episode(telemetry_store, ep_id, regime=reg)

    class _TelemetryServiceStub:
        def get_trade_episode_projection(
            self,
            trade_episode_id: str,
            *,
            as_of: str | None = None,
            as_of_sequence: int | None = None,
            tenant_id: str | None = None,
        ) -> dict[str, Any] | None:
            return telemetry_store.get(
                trade_episode_id,
                as_of=as_of,
                as_of_sequence=as_of_sequence,
                tenant_id=tenant_id,
            )

        def list_runtime_summaries(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr(telemetry_main, "_get_service", lambda: _TelemetryServiceStub())
    telemetry_client = telemetry_main.app.test_client()

    from services.consultation.meeting_outputs import _mint_jwt

    default_telemetry_token = _mint_jwt(
        subject="pantheon-dev-consultation-telemetry-reader",
        tenant_id=TENANT,
        roles=["viewer", "reviewer"],
        secret=SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        extra_claims={"allowed_tenants": [TENANT]},
    )
    default_telemetry_token_file = tmp_path / "default_consultation_telemetry_read_token"
    default_telemetry_token_file.write_text(default_telemetry_token, encoding="utf-8")
    os.chmod(default_telemetry_token_file, 0o600)
    monkeypatch.setenv("CONSULTATION_TELEMETRY_READ_TOKEN_FILE", str(default_telemetry_token_file))

    call_log: list[dict[str, Any]] = []

    def in_process_transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        parsed = urllib.parse.urlsplit(url)
        path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        call_log.append(
            {
                "method": method.upper(),
                "path": path_and_query,
                "headers": dict(headers),
                "body": copy.deepcopy(body),
            }
        )
        if parsed.path.startswith("/api/telemetry/"):
            flask_resp = telemetry_client.open(
                path_and_query,
                method=method.upper(),
                headers=dict(headers),
                json=body if method.upper() == "POST" else None,
            )
            data = flask_resp.get_json(silent=True)
            if not isinstance(data, dict):
                data = {"detail": flask_resp.get_data(as_text=True)}
            return flask_resp.status_code, data

        if parsed.path.startswith("/api/research/"):
            client = research_client
        elif parsed.path.startswith("/api/evolution/"):
            client = evolution_client
        elif parsed.path.startswith("/api/personas/"):
            client = persona_client
        elif parsed.path.startswith("/api/memory/"):
            client = memory_client
        elif parsed.path.startswith("/api/governance/"):
            client = governance_client
        elif parsed.path.startswith("/bff/agora/"):
            client = agora_client
        else:
            raise AssertionError(f"Unrouted path: {parsed.path}")

        if method.upper() == "POST":
            resp = client.post(path_and_query, headers=dict(headers), json=body)
        elif method.upper() == "GET":
            resp = client.get(path_and_query, headers=dict(headers))
        else:
            raise AssertionError(f"Unsupported HTTP method: {method}")

        try:
            data = resp.json()
        except Exception:
            data = {"detail": resp.text}
        return resp.status_code, data if isinstance(data, dict) else {"items": data}

    ledger_path = tmp_path / "meeting_output_dispatches.json"
    with output_routing_context(transport=in_process_transport, store_path=ledger_path):
        yield {
            "research_module": research_module,
            "research_client": research_client,
            "evolution_main": evolution_main,
            "evolution_client": evolution_client,
            "governance_main": governance_main,
            "governance_client": governance_client,
            "memory_main": memory_main,
            "memory_client": memory_client,
            "telemetry_store": telemetry_store,
            "telemetry_client": telemetry_client,
            "lesson_gov_service": lesson_gov_service,
            "endorsed_lesson_id": endorsed_lesson_id,
            "persona_client": persona_client,
            "harness_owner": harness_owner,
            "pool_store": pool_store,
            "agora_service": agora_service,
            "agora_client": agora_client,
            "call_log": call_log,
            "ledger_path": ledger_path,
        }


def _all_eight_proposals(endorsed_lesson_id: str) -> list[dict[str, Any]]:
    case_file = _make_case_file([endorsed_lesson_id])
    return [
        {
            "proposal_id": "prop-research-1",
            "meeting_id": MEETING_ID,
            "kind": "research_demand",
            "sponsor_persona_id": SPONSOR,
            "payload": {
                "mandate": "momentum",
                "missing_datasets": ["tw-institutional-flow"],
                "tried_families": ["momentum", "breakout"],
                "short_measure": "effective_bets",
                "pool_version": 1,
            },
            "citations": ["casefile-item-1"],
        },
        {
            "proposal_id": "prop-evolution-1",
            "meeting_id": MEETING_ID,
            "kind": "evolution_proposal",
            "sponsor_persona_id": SPONSOR,
            "payload": {
                "target_type": "candidate_artifact",
                "target_id": "artifact-alpha-v1",
                "target_version": "1.0.0",
                "action_type": "retrain",
                "rationale": "Retrain momentum model on post-regime window.",
            },
            "citations": ["memo-mt1-001"],
        },
        {
            "proposal_id": "prop-harness-1",
            "meeting_id": MEETING_ID,
            "kind": "harness_patch",
            "sponsor_persona_id": SPONSOR,
            "payload": {
                "base_version": 1,
                "rollback_target": 1,
                "patch_type": "threshold",
                "proposed_change": {
                    "cycle_thresholds": {
                        "minimum_evidence_count": 4,
                        "minimum_confidence": 0.85,
                    }
                },
                "expected_effect": "Raise evidence threshold after optimization review.",
            },
            "citations": [{"kind": "memo", "ref": "memo-mt2-001", "moment": MEETING_ID}],
        },
        {
            "proposal_id": "prop-lesson-cand-1",
            "meeting_id": MEETING_ID,
            "kind": "lesson_candidate",
            "sponsor_persona_id": SPONSOR,
            "payload": {
                "scope": "strategy",
                "proposed_change": "Avoid chasing gap-up openings when breadth is negative.",
                "confidence": 0.82,
            },
            "citations": ["ep-101", "ep-102", "ep-103"],
        },
        {
            "proposal_id": "prop-lesson-rule-1",
            "meeting_id": MEETING_ID,
            "kind": "lesson_rule",
            "sponsor_persona_id": SPONSOR,
            "payload": {
                "kind": "gate_check",
                "lesson_ids": [endorsed_lesson_id],
                "case_file": case_file,
                "expected_effect": "Require dual regime confirmation at validation gate.",
                "gate_check": {
                    "gate": "research_validation",
                    "check_name": "dual_regime_confirmation",
                    "description": "Require positive expectancy across two regimes.",
                    "parameters": {"min_regimes": 2},
                },
            },
            "citations": [endorsed_lesson_id],
        },
        {
            "proposal_id": "prop-pool-dec-1",
            "meeting_id": MEETING_ID,
            "kind": "pool_decision",
            "sponsor_persona_id": SPONSOR,
            "payload": {
                "expected_version": 1,
                "action": "reweight",
                "books": {
                    "strat-member-1": {
                        "checksum": "sha256:" + "a" * 64,
                        "source_ref": "paper-ledger-1",
                        "basis": "underlying_exposure_fraction_net_taiwan_costs_equal_notional",
                        "since_inception": True,
                        "dates": ["2026-10-05", "2026-10-06", "2026-10-07"],
                        "returns": [0.005, -0.002, 0.004],
                    }
                },
                "observations": [
                    {
                        "date": "2026-10-07",
                        "gross_notional": 300000.0,
                        "gross_limit": 1000000.0,
                        "drawdown": 0.01,
                        "drawdown_limit": 0.10,
                    }
                ],
            },
            "citations": ["pool-health-001"],
        },
        {
            "proposal_id": "prop-gov-apv-1",
            "meeting_id": MEETING_ID,
            "kind": "governance_approval",
            "sponsor_persona_id": SPONSOR,
            "payload": {
                "target_type": "strategy_spec",
                "target_id": "strat-spec-quarterly-1",
                "target_version": "1.0.0",
                "risk_level": "medium",
                "rationale": "Propose strategy spec for governance review.",
            },
            "citations": ["memo-mt6-001"],
        },
        {
            "proposal_id": "prop-agora-cand-1",
            "meeting_id": MEETING_ID,
            "kind": "agora_candidate_decision",
            "sponsor_persona_id": None,
            "payload": {
                "interaction_id": MEETING_ID,
                "opinion_id": "opinion-agora-1",
                "measure_id": "measure-agora-1",
                "owner_user_id": "user-a",
            },
            "citations": ["journal-101"],
        },
    ]


def test_dispatch_all_eight_proposal_kinds_to_real_owner_routes_and_read_back_live_status(
    owner_harness: dict[str, Any],
) -> None:
    proposals = _all_eight_proposals(owner_harness["endorsed_lesson_id"])
    assert {p["kind"] for p in proposals} == SUPPORTED_PROPOSAL_KINDS

    meeting = {
        "meeting_id": MEETING_ID,
        "tenant_id": TENANT,
        "meeting_type": "MT-1",
        "chair_persona_id": SPONSOR,
        "participants": [
            {"persona_id": SPONSOR, "role": "chair", "origin": "institutional"},
            {"persona_id": "persona-risk-officer", "role": "risk", "origin": "institutional"},
        ],
        "synthesis": {"proposals": proposals, "outcome": "completed"},
    }

    results = dispatch_proposals(meeting)
    assert len(results) == 8
    by_id = {r["proposal_id"]: r for r in results}

    # 1. Every proposal succeeded, has a downstream_id, and entered at its first state
    expected_initial_states = {
        "prop-research-1": "open",
        "prop-evolution-1": "proposed",
        "prop-harness-1": "draft",
        "prop-lesson-cand-1": "proposed",
        "prop-lesson-rule-1": "pending_governance_decision",
        "prop-pool-dec-1": "requires_approved_envelope",
        "prop-gov-apv-1": "proposed",
        "prop-agora-cand-1": "draft",
    }
    for prop_id, expected_state in expected_initial_states.items():
        res = by_id[prop_id]
        assert res["error"] is None, f"{prop_id} failed: {res['error']}"
        assert res["downstream_id"], f"{prop_id} missing downstream_id"
        assert res["status"] == expected_state
        assert res["status"] not in FORBIDDEN_APPROVED_OR_APPLIED_STATES

    # 2. Verify sponsor persona identity was recorded by each owner
    evo_dec = owner_harness["evolution_main"].store.get(by_id["prop-evolution-1"]["downstream_id"])
    assert evo_dec is not None and evo_dec.created_by_id == SPONSOR
    assert str(getattr(evo_dec.decision_state, "value", evo_dec.decision_state)) == "proposed"

    gov_dec = owner_harness["governance_main"].store.get(by_id["prop-gov-apv-1"]["downstream_id"])
    assert gov_dec is not None and gov_dec.owner_user_id == SPONSOR and gov_dec.persona_id == SPONSOR
    assert str(getattr(gov_dec.decision_state, "value", gov_dec.decision_state)) == "proposed" and gov_dec.decision is None

    harness_state = owner_harness["harness_owner"].read(SPONSOR)
    assert harness_state["active_version"] == 1  # Not activated
    assert harness_state["patches"][by_id["prop-harness-1"]["downstream_id"]]["status"] == "draft"

    pool_v2 = owner_harness["pool_store"].version(SPONSOR, TENANT, 2)
    assert pool_v2["created_by"] == SPONSOR
    assert pool_v2["decision"]["execution_status"] == "requires_approved_envelope"

    # 3. Idempotent replay: calling dispatch_proposals again makes zero new POST calls
    post_count_before = sum(1 for c in owner_harness["call_log"] if c["method"] == "POST")
    replay_results = dispatch_proposals(meeting)
    post_count_after = sum(1 for c in owner_harness["call_log"] if c["method"] == "POST")
    assert post_count_after == post_count_before
    assert replay_results == results

    # 4. Read back live status from each owner route and verify meeting record updates when owner state advances
    for prop in proposals:
        pid = prop["proposal_id"]
        status_info = read_downstream_status(pid)
        assert status_info["downstream_id"] == by_id[pid]["downstream_id"]
        assert status_info["status"] == expected_initial_states[pid]
        assert status_info["updated_at"]

    # Advance several downstream records on their owners and verify read_downstream_status reflects live changes
    # (a) Satisfy research demand via reconcile
    owner_harness["research_module"].research_demand_store.reconcile(
        TENANT,
        SPONSOR,
        [{"artifact_ref": "strat-new-1", "joined_at": "2026-12-01T00:00:00Z"}],
    )
    live_research = read_downstream_status("prop-research-1")
    assert live_research["status"] == "satisfied"
    assert meeting["synthesis"]["proposals"][0]["status"] == "satisfied"
    assert meeting["synthesis"]["proposals"][0]["downstream"]["status"] == "satisfied"

    # (b) Precheck harness patch -> previewed
    patch_id = by_id["prop-harness-1"]["downstream_id"]
    owner_harness["harness_owner"].operate(SPONSOR, patch_id, "precheck", SPONSOR)
    live_harness = read_downstream_status("prop-harness-1")
    assert live_harness["status"] == "previewed"
    assert meeting["synthesis"]["proposals"][2]["status"] == "previewed"

    # (c) Submit lesson candidate for review -> pending_review
    lc_id = by_id["prop-lesson-cand-1"]["downstream_id"]
    owner_harness["lesson_gov_service"].submit_review(lc_id, content={"action": "submit_review"})
    live_lesson = read_downstream_status("prop-lesson-cand-1")
    assert live_lesson["status"] == "pending_review"
    assert meeting["synthesis"]["proposals"][3]["status"] == "pending_review"

    # (d) Agora candidate decision -> review_requested
    agora_id = by_id["prop-agora-cand-1"]["downstream_id"]
    cand = owner_harness["agora_service"].store.get(agora_id, TENANT, "user-a")
    assert cand is not None
    owner_harness["agora_service"].decide(
        proposal_id=agora_id,
        command=CandidateDecisionCommand(
            action="accept_for_review",
            reason="Trader accepted candidate for validation review",
            expected_revision=cand["revision"],
            expected_proposal_digest=cand["proposal_digest"],
        ),
        tenant_id=TENANT,
        owner_user_id="user-a",
        actor_id="operator-a",
        expected_etag=owner_harness["agora_service"].store.etag(cand),
        idempotency_key="agora-accept-1",
    )
    live_agora = read_downstream_status("prop-agora-cand-1")
    assert live_agora["status"] == "review_requested"
    assert meeting["synthesis"]["proposals"][7]["status"] == "review_requested"

    listed = list_dispatched_proposals(meeting_id=MEETING_ID)
    assert len(listed) == 8


def test_advisors_and_private_servants_never_sponsor_and_agora_requires_none_sponsor(
    owner_harness: dict[str, Any],
) -> None:
    meeting = {
        "meeting_id": "mtg-sponsor-guard-001",
        "tenant_id": TENANT,
        "participants": [
            {"persona_id": SPONSOR, "role": "chair", "origin": "institutional"},
            {"persona_id": "p-macro-guest", "role": "participant", "origin": "agora_advisor"},
            {
                "persona_id": "p-user-helper",
                "role": "participant",
                "origin": "private_servant",
                "agora_user_id": "user-a",
            },
        ],
        "proposals": [
            {
                "proposal_id": "prop-bad-advisor-roster",
                "meeting_id": "mtg-sponsor-guard-001",
                "kind": "research_demand",
                "sponsor_persona_id": "p-macro-guest",
                "payload": {"mandate": "macro"},
                "citations": ["c1"],
            },
            {
                "proposal_id": "prop-bad-servant-roster",
                "meeting_id": "mtg-sponsor-guard-001",
                "kind": "evolution_proposal",
                "sponsor_persona_id": "p-user-helper",
                "payload": {"target_type": "persona", "target_id": "p-user-helper", "action_type": "retrain"},
                "citations": ["c1"],
            },
            {
                "proposal_id": "prop-bad-advisor-name",
                "meeting_id": "mtg-sponsor-guard-001",
                "kind": "harness_patch",
                "sponsor_persona_id": "agora-advisor-trend",
                "payload": {"base_version": 1},
                "citations": ["c1"],
            },
            {
                "proposal_id": "prop-bad-servant-name",
                "meeting_id": "mtg-sponsor-guard-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": "private-servant-01",
                "payload": {"scope": "strategy", "proposed_change": "x"},
                "citations": ["c1"],
            },
            {
                "proposal_id": "prop-bad-missing-sponsor",
                "meeting_id": "mtg-sponsor-guard-001",
                "kind": "governance_approval",
                "sponsor_persona_id": None,
                "payload": {"target_type": "strategy_spec", "target_id": "s1"},
                "citations": ["c1"],
            },
            {
                "proposal_id": "prop-bad-agora-with-sponsor",
                "meeting_id": "mtg-sponsor-guard-001",
                "kind": "agora_candidate_decision",
                "sponsor_persona_id": SPONSOR,
                "payload": {"interaction_id": MEETING_ID, "opinion_id": "opinion-agora-1", "measure_id": "measure-agora-1"},
                "citations": ["c1"],
            },
            {
                "proposal_id": "prop-bad-impersonation",
                "meeting_id": "mtg-sponsor-guard-001",
                "kind": "research_demand",
                "sponsor_persona_id": SPONSOR,
                "payload": {"persona_id": "persona-other", "mandate": "momentum"},
                "citations": ["c1"],
            },
        ],
    }

    post_before = sum(1 for c in owner_harness["call_log"] if c["method"] == "POST")
    results = dispatch_proposals(meeting)
    post_after = sum(1 for c in owner_harness["call_log"] if c["method"] == "POST")

    assert post_after == post_before  # Zero owner writes executed for invalid sponsors
    for res in results:
        assert res["status"] == "failed"
        assert res["downstream_id"] is None
        assert res["error"]


def test_dispatch_failure_is_recorded_and_retried_idempotently(
    owner_harness: dict[str, Any],
) -> None:
    base_transport = owner_harness["ledger_path"]
    call_log = owner_harness["call_log"]
    fail_once = {"evolution": True}

    from services.consultation import meeting_outputs as mo_module

    real_transport = mo_module._CONFIGURED_TRANSPORT
    assert real_transport is not None

    def flaky_transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if method == "POST" and "/api/evolution/proposals" in url and fail_once["evolution"]:
            fail_once["evolution"] = False
            call_log.append({"method": "POST", "path": "/api/evolution/proposals", "simulated_failure": True})
            return 503, {"detail": "evolution service temporarily unavailable"}
        return real_transport(method, url, headers, body)

    meeting = {
        "meeting_id": "mtg-retry-001",
        "tenant_id": TENANT,
        "proposals": [
            {
                "proposal_id": "prop-retry-evo-1",
                "meeting_id": "mtg-retry-001",
                "kind": "evolution_proposal",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-retry-v1",
                    "target_version": "1.0.0",
                    "action_type": "observe",
                    "rationale": "Observe candidate artifact after meeting.",
                },
                "citations": ["memo-retry-001"],
            },
            {
                "proposal_id": "prop-retry-rd-1",
                "meeting_id": "mtg-retry-001",
                "kind": "research_demand",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "mandate": "mean_reversion",
                    "missing_datasets": ["tw-borrow"],
                    "tried_families": ["mean_reversion"],
                    "short_measure": "idle_day_share",
                    "pool_version": 1,
                },
                "citations": ["memo-retry-002"],
            },
        ],
    }

    with output_routing_context(transport=flaky_transport, store_path=base_transport):
        # First call: evolution fails, research_demand succeeds
        first_results = dispatch_proposals(meeting)
        by_id_first = {r["proposal_id"]: r for r in first_results}
        assert by_id_first["prop-retry-evo-1"]["status"] == "failed"
        assert by_id_first["prop-retry-evo-1"]["downstream_id"] is None
        assert "503" in str(by_id_first["prop-retry-evo-1"]["error"])
        assert meeting["proposals"][0]["status"] == "failed"
        assert meeting["proposals"][0]["error"] is not None

        assert by_id_first["prop-retry-rd-1"]["status"] == "open"
        assert by_id_first["prop-retry-rd-1"]["downstream_id"] is not None
        rd_id = by_id_first["prop-retry-rd-1"]["downstream_id"]

        failed_read = read_downstream_status("prop-retry-evo-1")
        assert failed_read["status"] == "failed" and failed_read["downstream_id"] is None

        # Second call: retries only the failed evolution proposal and keeps research_demand untouched
        rd_posts_before = sum(1 for c in call_log if c.get("path") == "/api/research/persona-demands" and c["method"] == "POST")
        second_results = dispatch_proposals(meeting)
        rd_posts_after = sum(1 for c in call_log if c.get("path") == "/api/research/persona-demands" and c["method"] == "POST")
        assert rd_posts_after == rd_posts_before  # research_demand was NOT re-dispatched

        by_id_second = {r["proposal_id"]: r for r in second_results}
        assert by_id_second["prop-retry-evo-1"]["status"] == "proposed"
        assert by_id_second["prop-retry-evo-1"]["error"] is None
        assert by_id_second["prop-retry-evo-1"]["downstream_id"] == "evolution-proposal-from-prop-retry-evo-1"
        assert by_id_second["prop-retry-rd-1"]["downstream_id"] == rd_id
        assert meeting["proposals"][0]["status"] == "proposed"
        assert meeting["proposals"][0]["error"] is None


def test_downstream_gates_unchanged_even_when_payload_requests_approved_or_applied(
    owner_harness: dict[str, Any],
) -> None:
    meeting = {
        "meeting_id": "mtg-gate-guard-001",
        "tenant_id": TENANT,
        "proposals": [
            {
                "proposal_id": "prop-gate-lesson-1",
                "meeting_id": "mtg-gate-guard-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Never bypass memory governance gate",
                    "confidence": 0.9,
                    "review_state": "endorsed",
                    "promotion_stage": "live_approved",
                    "target_env": "live",
                },
                "citations": ["ep-201", "ep-202", "ep-203"],
            },
            {
                "proposal_id": "prop-gate-gov-1",
                "meeting_id": "mtg-gate-guard-001",
                "kind": "governance_approval",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "target_type": "strategy_spec",
                    "target_id": "strat-spec-gate-1",
                    "target_version": "1.0.0",
                    "risk_level": "high",
                    "decision_state": "decided",
                    "decision": "approved",
                },
                "citations": ["memo-gate-001"],
            },
        ],
    }

    results = dispatch_proposals(meeting)
    by_id = {r["proposal_id"]: r for r in results}
    assert by_id["prop-gate-lesson-1"]["status"] == "proposed"
    assert by_id["prop-gate-gov-1"]["status"] == "proposed"

    lc_record = owner_harness["lesson_gov_service"].store.get(by_id["prop-gate-lesson-1"]["downstream_id"])
    assert lc_record is not None
    assert lc_record["review_state"] == "proposed"
    assert lc_record["promotion_stage"] == "proposed"
    assert lc_record["target_env"] == "paper"

    gov_record = owner_harness["governance_main"].store.get(by_id["prop-gate-gov-1"]["downstream_id"])
    assert gov_record is not None
    assert str(getattr(gov_record.decision_state, "value", gov_record.decision_state)) == "proposed"
    assert gov_record.decision is None


def test_configured_service_tokens_and_real_episode_reference_requirement_for_lesson_candidates(
    owner_harness: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.issue_dev_paper_principals import issue_environment

    dev_tenant = "tenant-dev"
    issued = issue_environment(
        {
            "PANTHEON_ENV": "dev",
            "PANTHEON_DEV_BFF_TENANT_ID": dev_tenant,
            "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
            "PANTHEON_DEV_BFF_JWT_SECRET": SECRET,
            "PANTHEON_DEV_BFF_JWT_ISSUER": ISSUER,
            "PANTHEON_DEV_BFF_JWT_AUDIENCE": AUDIENCE,
        }
    )
    research_jwt = issued["PERSONA_RESEARCH_SERVICE_TOKEN"]
    memory_write_jwt = issued["CONSULTATION_MEMORY_WRITE_TOKEN"]
    memory_read_jwt = issued["CONSULTATION_MEMORY_READ_TOKEN"]
    telemetry_read_jwt = issued["CONSULTATION_TELEMETRY_READ_TOKEN"]

    write_file = tmp_path / "CONSULTATION_MEMORY_WRITE_TOKEN"
    write_file.write_text(memory_write_jwt, encoding="utf-8")
    os.chmod(write_file, 0o600)
    read_file = tmp_path / "CONSULTATION_MEMORY_READ_TOKEN"
    read_file.write_text(memory_read_jwt, encoding="utf-8")
    os.chmod(read_file, 0o600)
    telemetry_read_file = tmp_path / "CONSULTATION_TELEMETRY_READ_TOKEN"
    telemetry_read_file.write_text(telemetry_read_jwt, encoding="utf-8")
    os.chmod(telemetry_read_file, 0o600)

    monkeypatch.setenv("PANTHEON_RESEARCH_SERVICE_TOKEN", research_jwt)
    monkeypatch.setenv("CONSULTATION_MEMORY_WRITE_TOKEN_FILE", str(write_file))
    monkeypatch.setenv("CONSULTATION_MEMORY_READ_TOKEN_FILE", str(read_file))
    monkeypatch.setenv("CONSULTATION_TELEMETRY_READ_TOKEN_FILE", str(telemetry_read_file))

    ep_uuid_1 = str(uuid.uuid4())
    ep_uuid_2 = str(uuid.uuid4())
    ep_uuid_3 = str(uuid.uuid4())
    unverified_full_shape_id = str(uuid.uuid4())
    telemetry_store: TradeEpisodeProjectionStore = owner_harness["telemetry_store"]
    for ep_id, reg in (("ep-dev-301", "bull"), ("ep-dev-302", "bear"), ("ep-dev-303", "bull")):
        _seed_telemetry_episode(telemetry_store, ep_id, tenant_id=dev_tenant, persona_id=SPONSOR, regime=reg)
    verified_ep_1 = _seed_telemetry_episode(telemetry_store, ep_uuid_1, tenant_id=dev_tenant, persona_id=SPONSOR, regime="bull")
    verified_ep_2 = _seed_telemetry_episode(telemetry_store, ep_uuid_2, tenant_id=dev_tenant, persona_id=SPONSOR, regime="bear")
    verified_ep_3 = _seed_telemetry_episode(telemetry_store, ep_uuid_3, tenant_id=dev_tenant, persona_id=SPONSOR, regime="bull")
    foreign_persona_ep = _seed_telemetry_episode(
        telemetry_store,
        "ep-foreign-persona",
        tenant_id=dev_tenant,
        persona_id="persona-other",
        regime="bull",
    )
    _seed_telemetry_episode(
        telemetry_store,
        "ep-foreign-tenant",
        tenant_id="tenant-other",
        persona_id=SPONSOR,
        regime="bear",
    )
    # Full-shape candidate matching TradeEpisodeProjection schema, tenant, and sponsor persona,
    # but NOT present in the authoritative Telemetry store.
    unverified_full_shape_ep = {
        **copy.deepcopy(verified_ep_1),
        "trade_episode_id": unverified_full_shape_id,
    }

    meeting = {
        "meeting_id": "mtg-hosted-tokens-001",
        "tenant_id": dev_tenant,
        "case_file_items": [
            {
                "item_id": "facts:strategy_pool:persona-alpha@1",
                "group": "facts",
                "kind": "strategy_pool",
                "source_owner": "persona",
                "source_id": "persona-alpha@1",
                "excerpt": '{"pool_version":1,"members":[{"artifact_ref":"strat-member-1","stage":"active"}]}',
                "subject_fields": {"pool_version": 1},
            },
            verified_ep_1,
            verified_ep_2,
            verified_ep_3,
            unverified_full_shape_ep,
            foreign_persona_ep,
        ],
        "proposals": [
            {
                "proposal_id": "prop-hosted-rd-1",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "research_demand",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "mandate": "momentum",
                    "missing_datasets": [],
                    "tried_families": ["momentum"],
                    "short_measure": "effective_bets",
                    "pool_version": 1,
                },
                "citations": ["facts:strategy_pool:persona-alpha@1"],
            },
            {
                "proposal_id": "prop-hosted-lc-valid-citations",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Lesson candidate citing three real episode references verified via Telemetry",
                    "confidence": 0.84,
                },
                "citations": [
                    "facts:strategy_pool:persona-alpha@1",
                    "ep-dev-301",
                    "ep-dev-302",
                    "ep-dev-301",
                    "ep-dev-303",
                ],
            },
            {
                "proposal_id": "prop-hosted-lc-valid-verified-evidence",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Lesson candidate passing full-shape TradeEpisodeProjection candidates verified via Telemetry GET",
                    "confidence": 0.86,
                    "trade_episode_ids": [verified_ep_1, verified_ep_2, verified_ep_3],
                },
                "citations": [ep_uuid_1, ep_uuid_2, ep_uuid_3, ep_uuid_1, "ep-foreign-persona"],
            },
            {
                "proposal_id": "prop-hosted-lc-unverified-full-shape",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Full-shape candidate absent from Telemetry must fail before Memory POST",
                    "confidence": 0.85,
                    "trade_episode_ids": [verified_ep_1, verified_ep_2, unverified_full_shape_ep],
                },
                "citations": [unverified_full_shape_ep],
            },
            {
                "proposal_id": "prop-hosted-lc-non-episode-citation",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Non-episode strategy-pool citation must not be converted into episode UUIDs",
                    "confidence": 0.84,
                },
                "citations": ["facts:strategy_pool:persona-alpha@1"],
            },
            {
                "proposal_id": "prop-hosted-lc-no-evidence",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "No-evidence lesson candidate must fail closed without placeholder padding",
                    "confidence": 0.81,
                },
                "citations": [],
            },
            {
                "proposal_id": "prop-hosted-lc-insufficient-episodes",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Two distinct episodes with duplicates must not be padded to three",
                    "confidence": 0.80,
                },
                "citations": ["ep-dev-301", "ep-dev-302", "ep-dev-301", "facts:strategy_pool:persona-alpha@1"],
            },
            {
                "proposal_id": "prop-hosted-lc-missing-and-foreign-episodes",
                "meeting_id": "mtg-hosted-tokens-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Missing, foreign-persona, and foreign-tenant episodes must be rejected before Memory submission",
                    "confidence": 0.83,
                    "trade_episode_ids": [
                        "ep-dev-301",
                        "ep-dev-302",
                        "ep-missing-404",
                        "ep-foreign-persona",
                        "ep-foreign-tenant",
                    ],
                },
                "citations": ["ep-missing-404", "ep-foreign-persona", "ep-foreign-tenant"],
            },
        ],
    }

    candidates_before = len(owner_harness["lesson_gov_service"].store.list())
    owner_harness["call_log"].clear()
    results = dispatch_proposals(meeting)
    by_id = {r["proposal_id"]: r for r in results}

    # 1. Research demand and the two valid lesson_candidate proposals succeed
    assert by_id["prop-hosted-rd-1"]["error"] is None
    assert by_id["prop-hosted-rd-1"]["status"] == "open"
    assert by_id["prop-hosted-lc-valid-citations"]["error"] is None
    assert by_id["prop-hosted-lc-valid-citations"]["status"] == "proposed"
    assert by_id["prop-hosted-lc-valid-verified-evidence"]["error"] is None
    assert by_id["prop-hosted-lc-valid-verified-evidence"]["status"] == "proposed"

    # 2. Unverified full-shape candidate, non-episode citation, no-evidence, <3 distinct episodes, and missing/foreign episodes fail closed before Memory submission
    for failed_pid, expected_count in (
        ("prop-hosted-lc-unverified-full-shape", 2),
        ("prop-hosted-lc-non-episode-citation", 0),
        ("prop-hosted-lc-no-evidence", 0),
        ("prop-hosted-lc-insufficient-episodes", 2),
        ("prop-hosted-lc-missing-and-foreign-episodes", 2),
    ):
        assert by_id[failed_pid]["status"] == "failed"
        assert by_id[failed_pid]["downstream_id"] is None
        assert (
            by_id[failed_pid]["error"]
            == "lesson_candidate requires at least 3 distinct supporting trade_episode_ids "
            f"from input or verified evidence (got {expected_count})"
        )

    rd_call = next(c for c in owner_harness["call_log"] if c["path"] == "/api/research/persona-demands" and c["method"] == "POST")
    assert rd_call["headers"]["Authorization"] == f"Bearer {research_jwt}"
    lc_calls = [c for c in owner_harness["call_log"] if c["path"] == "/api/memory/trade-lessons" and c["method"] == "POST"]
    assert len(lc_calls) == 2
    for lc_call in lc_calls:
        assert lc_call["headers"]["Authorization"] == f"Bearer {memory_write_jwt}"

    telemetry_calls = [c for c in owner_harness["call_log"] if c["path"].startswith("/api/telemetry/trade-episodes/") and c["method"] == "GET"]
    assert telemetry_calls
    for tel_call in telemetry_calls:
        assert tel_call["headers"]["Authorization"] == f"Bearer {telemetry_read_jwt}"
    # Every selected candidate ID (including full-shape candidates) was verified via Telemetry owner GET
    fetched_paths = {c["path"] for c in telemetry_calls}
    assert f"/api/telemetry/trade-episodes/{ep_uuid_1}" in fetched_paths
    assert f"/api/telemetry/trade-episodes/{ep_uuid_2}" in fetched_paths
    assert f"/api/telemetry/trade-episodes/{ep_uuid_3}" in fetched_paths
    assert f"/api/telemetry/trade-episodes/{unverified_full_shape_id}" in fetched_paths

    # Only the two valid candidates were persisted in Memory, with their exact real episode IDs
    assert len(owner_harness["lesson_gov_service"].store.list()) == candidates_before + 2
    lc_id_1 = by_id["prop-hosted-lc-valid-citations"]["downstream_id"]
    lc_record_1 = owner_harness["lesson_gov_service"].store.get(lc_id_1)
    assert lc_record_1 is not None
    assert lc_record_1["trade_episode_ids"] == ["ep-dev-301", "ep-dev-302", "ep-dev-303"]

    lc_id_2 = by_id["prop-hosted-lc-valid-verified-evidence"]["downstream_id"]
    lc_record_2 = owner_harness["lesson_gov_service"].store.get(lc_id_2)
    assert lc_record_2 is not None
    assert lc_record_2["trade_episode_ids"] == [ep_uuid_1, ep_uuid_2, ep_uuid_3]

    # Live readback succeeds using CONSULTATION_MEMORY_READ_TOKEN_FILE
    live_lc = read_downstream_status("prop-hosted-lc-valid-citations")
    assert live_lc["status"] == "proposed"
    assert live_lc["downstream_id"] == lc_id_1
    lc_get_call = next(c for c in owner_harness["call_log"] if c["path"].startswith("/api/memory/trade-lessons/") and c["method"] == "GET")
    assert lc_get_call["headers"]["Authorization"] == f"Bearer {memory_read_jwt}"

    # 3. Issued CONSULTATION_TELEMETRY_READ_TOKEN is denied on all real Telemetry write routes
    telemetry_client = owner_harness["telemetry_client"]
    tel_headers = {"Authorization": f"Bearer {telemetry_read_jwt}", "X-Tenant-Id": dev_tenant}
    for write_path in (
        "/api/telemetry/ingest",
        "/api/telemetry/ingest/batch",
        "/api/v1/telemetry/infrastructure-health",
        "/api/v1/telemetry/heartbeats",
        "/api/telemetry/replay",
    ):
        write_resp = telemetry_client.post(write_path, json={}, headers=tel_headers)
        assert write_resp.status_code == 403
        assert write_resp.get_json()["error"]["code"] == "AUTH_FORBIDDEN"

    # 4. When consultation telemetry read credentials are unconfigured, no fallback JWT is minted
    # and missing configuration reports unavailable evidence before any Memory POST.
    for env_key in (
        "CONSULTATION_TELEMETRY_READ_TOKEN",
        "CONSULTATION_TELEMETRY_READ_TOKEN_FILE",
        "PANTHEON_TELEMETRY_SERVICE_TOKEN",
        "PANTHEON_TELEMETRY_SERVICE_TOKEN_FILE",
    ):
        monkeypatch.delenv(env_key, raising=False)

    unconfigured_meeting = {
        "meeting_id": "mtg-unconfigured-telemetry-001",
        "tenant_id": dev_tenant,
        "proposals": [
            {
                "proposal_id": "prop-lc-unconfigured-telemetry",
                "meeting_id": "mtg-unconfigured-telemetry-001",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Unconfigured telemetry credential must not mint fallback JWT",
                    "confidence": 0.85,
                    "trade_episode_ids": [verified_ep_1, verified_ep_2, verified_ep_3],
                },
                "citations": ["ep-dev-301", "ep-dev-302", "ep-dev-303"],
            }
        ],
    }
    owner_harness["call_log"].clear()
    unconf_results = dispatch_proposals(unconfigured_meeting)
    assert len(unconf_results) == 1
    assert unconf_results[0]["status"] == "failed"
    assert unconf_results[0]["downstream_id"] is None
    assert (
        unconf_results[0]["error"]
        == "lesson_candidate requires at least 3 distinct supporting trade_episode_ids "
        "from input or verified evidence (got 0)"
    )
    assert owner_harness["call_log"] == []


@pytest.mark.parametrize("failure_mode", ["missing", "unsafe_mode", "revoked_empty"])
def test_configured_token_file_failures_fail_closed_without_alias_or_jwt_fallback(
    owner_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_mode: str,
) -> None:
    """A configured missing, unsafe, or revoked *_FILE credential must fail closed.

    Even when lower-priority alias tokens and JWT signing secrets are present in
    the environment, dispatch_proposals must record a dispatch failure and send
    zero outbound HTTP requests rather than falling back to an alias or minting
    a synthetic JWT.
    """
    from services.consultation.meeting_outputs import MeetingOutputRoutingError, _mint_jwt

    alias_memory_jwt = _mint_jwt(
        subject="pantheon-dev-consultation-memory-writer",
        tenant_id=TENANT,
        roles=["consultation-svc"],
        secret=SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        extra_claims={"marker": "alias-memory-token-must-not-be-used"},
    )
    alias_research_jwt = _mint_jwt(
        subject="persona-pool-cycle",
        tenant_id=TENANT,
        roles=["viewer"],
        secret=SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        extra_claims={"marker": "alias-research-token-must-not-be-used"},
    )

    bad_mem_file = tmp_path / f"CONSULTATION_MEMORY_WRITE_TOKEN_{failure_mode}"
    bad_res_file = tmp_path / f"PERSONA_RESEARCH_SERVICE_TOKEN_{failure_mode}"
    if failure_mode == "unsafe_mode":
        bad_mem_file.write_text(alias_memory_jwt, encoding="utf-8")
        os.chmod(bad_mem_file, 0o644)
        bad_res_file.write_text(alias_research_jwt, encoding="utf-8")
        os.chmod(bad_res_file, 0o644)
    elif failure_mode == "revoked_empty":
        bad_mem_file.write_text("   \n", encoding="utf-8")
        os.chmod(bad_mem_file, 0o600)
        bad_res_file.write_text("", encoding="utf-8")
        os.chmod(bad_res_file, 0o600)

    # Configure higher-priority *_FILE paths to the broken files while also
    # populating fallback aliases and JWT secrets to prove no fallback occurs.
    monkeypatch.setenv("CONSULTATION_MEMORY_WRITE_TOKEN_FILE", str(bad_mem_file))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", alias_memory_jwt)
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", SECRET)

    monkeypatch.setenv("PERSONA_RESEARCH_SERVICE_TOKEN_FILE", str(bad_res_file))
    monkeypatch.setenv("PANTHEON_RESEARCH_SERVICE_TOKEN", alias_research_jwt)
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", SECRET)

    meeting = {
        "meeting_id": f"mtg-fail-closed-{failure_mode}",
        "tenant_id": TENANT,
        "proposals": [
            {
                "proposal_id": f"prop-fc-rd-{failure_mode}",
                "meeting_id": f"mtg-fail-closed-{failure_mode}",
                "kind": "research_demand",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "mandate": "momentum",
                    "missing_datasets": [],
                    "tried_families": ["momentum"],
                    "short_measure": "effective_bets",
                    "pool_version": 1,
                },
                "citations": ["facts:strategy_pool:persona-alpha@1"],
            },
            {
                "proposal_id": f"prop-fc-lc-{failure_mode}",
                "meeting_id": f"mtg-fail-closed-{failure_mode}",
                "kind": "lesson_candidate",
                "sponsor_persona_id": SPONSOR,
                "payload": {
                    "scope": "strategy",
                    "proposed_change": "Must not mint fallback JWT when token file fails",
                    "confidence": 0.85,
                },
                "citations": ["ep-301", "ep-302", "ep-303"],
            },
        ],
    }

    owner_harness["call_log"].clear()
    results = dispatch_proposals(meeting)
    by_id = {r["proposal_id"]: r for r in results}

    for pid in (f"prop-fc-rd-{failure_mode}", f"prop-fc-lc-{failure_mode}"):
        assert by_id[pid]["status"] == "failed"
        assert by_id[pid]["downstream_id"] is None
        assert by_id[pid]["error"] == "Configured service credential unavailable"

    # Zero HTTP requests were sent to any owner (no alias token, no minted JWT).
    assert owner_harness["call_log"] == []

    # After repairing the configured files in place with valid mode-0600 tokens,
    # retrying dispatch_proposals succeeds and uses the file tokens.
    bad_mem_file.write_text(alias_memory_jwt, encoding="utf-8")
    os.chmod(bad_mem_file, 0o600)
    bad_res_file.write_text(alias_research_jwt, encoding="utf-8")
    os.chmod(bad_res_file, 0o600)

    retry_results = dispatch_proposals(meeting)
    retry_by_id = {r["proposal_id"]: r for r in retry_results}
    assert retry_by_id[f"prop-fc-rd-{failure_mode}"]["status"] == "open"
    assert retry_by_id[f"prop-fc-rd-{failure_mode}"]["error"] is None
    assert retry_by_id[f"prop-fc-lc-{failure_mode}"]["status"] == "proposed"
    assert retry_by_id[f"prop-fc-lc-{failure_mode}"]["error"] is None

    # Configuring a broken CONSULTATION_MEMORY_READ_TOKEN_FILE also fails closed
    # on read_downstream_status even when CONSULTATION_MEMORY_WRITE_TOKEN_FILE is valid.
    bad_read_file = tmp_path / f"CONSULTATION_MEMORY_READ_TOKEN_{failure_mode}"
    if failure_mode == "unsafe_mode":
        bad_read_file.write_text(alias_memory_jwt, encoding="utf-8")
        os.chmod(bad_read_file, 0o644)
    elif failure_mode == "revoked_empty":
        bad_read_file.write_text("", encoding="utf-8")
        os.chmod(bad_read_file, 0o600)
    monkeypatch.setenv("CONSULTATION_MEMORY_READ_TOKEN_FILE", str(bad_read_file))

    owner_harness["call_log"].clear()
    with pytest.raises(MeetingOutputRoutingError, match="Configured service credential unavailable"):
        read_downstream_status(f"prop-fc-lc-{failure_mode}")
    assert owner_harness["call_log"] == []
