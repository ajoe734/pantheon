from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.personas.service import (
    _bff_error,
    _dry_run_success_response,
    _extract_identity,
    _require_operator_role,
    _require_read_role,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from services.runtime_auth_inbound import encode_jwt_hs256


OPERATOR_HEADERS = {"Authorization": "Bearer dry-run-op:operator,approver,reviewer,admin"}


class DryRunRBACTestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._data: dict[str, Any] = seed_data or {}

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "source": "local_snapshot", "snapshot_at": snapshot_at}

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, {})

    def create_agora_signal(self, *, signal_id: str, title: str, body: str, actor_id: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        sig = {
            "id": signal_id,
            "signal_id": signal_id,
            "title": title,
            "body": body,
            "actor_id": actor_id,
            "reviewStatus": "pending_trader_review",
            **payload,
        }
        ds = self._get_dataset("agora_signals")
        if isinstance(ds, dict):
            ds[signal_id] = sig
        return sig

    def list_agora_signals(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("agora_signals")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_agora_signal(self, signal_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("agora_signals")
        if isinstance(ds, dict):
            return ds.get(str(signal_id or ""))
        return next((s for s in ds if s.get("id") == signal_id or s.get("signal_id") == signal_id), None)

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("agora_sessions")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_agora_session(self, session_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("agora_sessions")
        if isinstance(ds, dict):
            return ds.get(str(session_id or ""))
        return next((s for s in ds if s.get("sessionId") == session_id or s.get("session_id") == session_id or s.get("id") == session_id), None)

    def list_strategy_specs(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("strategy_specs")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_strategy_spec(self, spec_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("strategy_specs")
        if isinstance(ds, dict):
            return ds.get(str(spec_id or ""))
        return next((s for s in ds if s.get("id") == spec_id or s.get("strategy_id") == spec_id), None)

    def get_strategy_spec_detail(self, strategy_id: str | None, *, version_selector: str | None = None) -> dict[str, Any] | None:
        spec = self.get_strategy_spec(strategy_id)
        if not spec:
            return None
        versions = spec.get("versions") or [spec]
        return versions[0]

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("personas")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("personas")
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("capital_pools")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_capital_pool(self, pool_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("capital_pools")
        if isinstance(ds, dict):
            return ds.get(str(pool_id or ""))
        return next((p for p in ds if p.get("id") == pool_id or p.get("pool_id") == pool_id), None)

    def list_ranking_formulas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("ranking_formulas")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_ranking_formula(self, formula_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("ranking_formulas")
        if isinstance(ds, dict):
            return ds.get(str(formula_id or ""))
        return next((f for f in ds if f.get("id") == formula_id or f.get("formula_id") == formula_id), None)

    def list_rebalances(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("rebalances")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_rebalance(self, rebalance_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("rebalances")
        if isinstance(ds, dict):
            return ds.get(str(rebalance_id or ""))
        return next((r for r in ds if r.get("id") == rebalance_id or r.get("rebalance_id") == rebalance_id), None)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("runtime_bindings")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_runtime_binding(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("runtime_bindings")
        if isinstance(ds, dict):
            return ds.get(str(runtime_id or ""))
        return next((r for r in ds if r.get("id") == runtime_id or r.get("runtime_id") == runtime_id), None)

    def list_decision_journal_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("decision_journal_entries")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_research_notes(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("research_notes")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_insight_cards(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("insight_cards")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_agora_training_examples(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("agora_training_examples")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)


class _BffState:
    read_store: DryRunRBACTestReadPorts
    command_store: CommandStore
    _STRATEGY_BFF_OVERLAY: dict[str, Any] = {}
    _PERSONA_BFF_OVERLAY: dict[str, Any] = {}
    _SKILL_REGISTRY: dict[str, Any] = {}
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY: dict[str, Any] = {}
    _CAPITAL_BFF_IDEMPOTENCY: dict[str, Any] = {}
    _SKILLS_BFF_IDEMPOTENCY: dict[str, Any] = {}
    _AGORA_CORE_BFF_IDEMPOTENCY: dict[str, Any] = {}
    _GOV_BFF_IDEMPOTENCY: dict[str, Any] = {}
    _FINAL_CONTRACT_IDEMPOTENCY: dict[str, Any] = {}
    _sse_buffers: dict[str, deque[Any]] = {}


bff_state = _BffState()


def _seed_read_store(path: Path) -> DryRunRBACTestReadPorts:
    seed_data = {
        "agora_signals": {
            "sig-dry-seed": {
                "id": "sig-dry-seed",
                "signal_id": "sig-dry-seed",
                "title": "Seed signal",
                "body": "Existing signal for feedback dry-run.",
                "reviewStatus": "pending_trader_review",
            }
        },
        "agora_sessions": {
            "sess-dry-seed": {
                "id": "sess-dry-seed",
                "sessionId": "sess-dry-seed",
                "title": "Seed session",
                "status": "active",
                "messages": [],
            }
        },
        "decision_journal_entries": {},
        "research_notes": {},
        "insight_cards": {},
        "agora_training_examples": {},
        "capital_pools": {},
        "ranking_formulas": {},
        "rebalances": {},
        "runtime_bindings": {},
        "personas": {},
        "strategy_specs": {},
    }
    return DryRunRBACTestReadPorts(seed_data)


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    def _check_auth(request: Request, *, is_write: bool) -> Any:
        auth_hdr = request.headers.get("Authorization")
        identity = _extract_identity(auth_hdr)
        if is_write:
            _require_operator_role(identity)
        else:
            _require_read_role(identity)
        return identity

    def _is_dry_run(request: Request) -> bool:
        hdr = request.headers.get("X-Dry-Run", "")
        return hdr.strip().lower() in ("1", "true", "yes", "on")

    # Strategies
    @app.post("/bff/strategies")
    async def post_strategies(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        if not payload.get("name"):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Strategy spec requires name",
                "name is missing",
                precondition_failed="strategy_spec.name",
            )
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            strat_id = f"strategy-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": strat_id, "strategy_id": strat_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="strategy.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "strat-live"}})

    @app.get("/bff/strategies/{strategy_id}")
    def get_strategy(strategy_id: str, request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        spec = bff_state.read_store.get_strategy_spec(strategy_id)
        if not spec and strategy_id not in bff_state._STRATEGY_BFF_OVERLAY:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return JSONResponse(status_code=200, content={"data": spec or bff_state._STRATEGY_BFF_OVERLAY.get(strategy_id)})

    @app.get("/bff/strategies")
    def list_strategies(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"data": bff_state.read_store.list_strategy_specs()})

    # Personas
    @app.post("/bff/personas")
    async def post_personas(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        if not payload.get("name"):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Persona requires name",
                "name is missing",
                precondition_failed="persona.name",
            )
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            persona_id = f"persona-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": persona_id, "persona_id": persona_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="persona.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "persona-live"}})

    @app.get("/bff/personas/{persona_id}")
    def get_persona(persona_id: str, request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        p = bff_state.read_store.get_persona(persona_id)
        if not p and persona_id not in bff_state._PERSONA_BFF_OVERLAY:
            raise HTTPException(status_code=404, detail="Persona not found")
        return JSONResponse(status_code=200, content={"data": p or bff_state._PERSONA_BFF_OVERLAY.get(persona_id)})

    # Capital Pools
    @app.post("/bff/capital-pools")
    async def post_capital_pools(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            pool_id = f"pool-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": pool_id, "pool_id": pool_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="capital_pool.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "pool-live"}})

    @app.get("/bff/capital-pools")
    def list_capital_pools(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"data": bff_state.read_store.list_capital_pools()})

    # Ranking Formulas
    @app.post("/bff/ranking-formulas")
    async def post_ranking_formulas(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        if not payload.get("name"):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Ranking formula requires name",
                "name is missing",
                precondition_failed="ranking_formula.name",
            )
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            form_id = f"formula-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": form_id, "formula_id": form_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="ranking_formula.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "formula-live"}})

    @app.get("/bff/ranking-formulas")
    def list_ranking_formulas(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"data": bff_state.read_store.list_ranking_formulas()})

    # Skills
    @app.post("/bff/skills")
    async def post_skills(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            skill_id = f"skill-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": skill_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="skill.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "skill-live"}})

    @app.get("/bff/skills/{skill_id}")
    def get_skill(skill_id: str, request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        if skill_id not in bff_state._SKILL_REGISTRY:
            raise HTTPException(status_code=404, detail="Skill not found")
        return JSONResponse(status_code=200, content={"data": bff_state._SKILL_REGISTRY[skill_id]})

    # Agora Routes
    @app.post("/bff/agora/journal")
    async def post_agora_journal(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            jid = f"journal-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": jid, **payload},
                idempotency_key=idem_key,
                evidence_kind="agora.journal.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "journal-live"}})

    @app.get("/bff/agora/journal")
    def list_agora_journal(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"items": bff_state.read_store.list_decision_journal_entries()})

    @app.post("/bff/agora/notes")
    async def post_agora_notes(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            nid = f"note-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": nid, **payload},
                idempotency_key=idem_key,
                evidence_kind="agora.note.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "note-live"}})

    @app.get("/bff/agora/notes")
    def list_agora_notes(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"items": bff_state.read_store.list_research_notes()})

    @app.post("/bff/agora/insights")
    async def post_agora_insights(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            iid = f"insight-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": iid, **payload},
                idempotency_key=idem_key,
                evidence_kind="agora.insight.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "insight-live"}})

    @app.get("/bff/agora/insights")
    def list_agora_insights(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"items": bff_state.read_store.list_insight_cards()})

    @app.post("/bff/agora/sessions")
    async def post_agora_sessions(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            sid = f"sess-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": sid, "sessionId": sid, **payload},
                idempotency_key=idem_key,
                evidence_kind="agora.session.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "sess-live"}})

    @app.get("/bff/agora/sessions")
    def list_agora_sessions(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"items": bff_state.read_store.list_agora_sessions()})

    @app.post("/bff/agora/training-examples")
    async def post_agora_training_examples(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            tid = f"training-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"trainingExampleId": tid, "id": tid, **payload},
                idempotency_key=idem_key,
                evidence_kind="agora.training.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "training-live"}})

    @app.get("/bff/agora/training-examples")
    def list_agora_training_examples(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"items": bff_state.read_store.list_agora_training_examples()})

    @app.post("/bff/agora/signals/{signal_id}/feedback")
    async def post_agora_feedback(signal_id: str, request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        decision = payload.get("decision")
        confidence = payload.get("confidence")
        reason = payload.get("reason")
        if (decision == "disagree" and confidence and confidence >= 4 and not reason) or (
            decision == "flag_suspicious" and not reason
        ):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Signal feedback reason is required",
                "reason is required for high-confidence disagree",
                precondition_failed="signal_feedback.reason",
            )
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            return _dry_run_success_response(
                {
                    "feedback": {
                        "feedbackId": f"fb-{signal_id}",
                        "signalId": signal_id,
                        **payload,
                    }
                },
                idempotency_key=idem_key,
                evidence_kind="agora.signal.feedback",
            )
        return JSONResponse(status_code=200, content={"data": {"status": "recorded"}})

    @app.get("/bff/agora/signals")
    def list_agora_signals(request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        return JSONResponse(status_code=200, content={"items": bff_state.read_store.list_agora_signals(), "data": bff_state.read_store.list_agora_signals()})

    # Deployments
    @app.post("/bff/deployments")
    async def post_deployments(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            return _dry_run_success_response(
                {"command": "CreateDeployment", "id": payload.get("id", "deployment-dry")},
                idempotency_key=idem_key,
                evidence_kind="deployment.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "deploy-live"}})

    # Interventions
    @app.post("/bff/v5/interventions/{intervention_id}/claim")
    async def claim_intervention(intervention_id: str, request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            return _dry_run_success_response(
                {"command": "V5InterventionAction", "status": "accepted", "intervention_id": intervention_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="intervention.preview",
            )
        return JSONResponse(status_code=200, content={"data": {"status": "claimed"}})

    # Rebalances
    @app.post("/bff/rebalances")
    async def post_rebalances(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        rebal_id = f"rebal-dry-{uuid.uuid4().hex[:8]}"
        if _is_dry_run(request):
            resp = _dry_run_success_response(
                {"command": "RebalanceAction", "rebalance_id": rebal_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="rebalance.preview",
            )
            # Add top-level rebalance_id to match test assertion
            content = json.loads(resp.body.decode("utf-8"))
            content["rebalance_id"] = rebal_id
            return JSONResponse(status_code=200, content=content)
        return JSONResponse(status_code=201, content={"data": {"id": "rebal-live"}})

    @app.get("/bff/rebalances/{rebalance_id}")
    def get_rebalance(rebalance_id: str, request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        r = bff_state.read_store.get_rebalance(rebalance_id)
        if not r:
            raise HTTPException(status_code=404, detail="Rebalance not found")
        return JSONResponse(status_code=200, content={"data": r})

    # Runtimes
    @app.post("/bff/runtimes")
    async def post_runtimes(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            rt_id = f"runtime-dry-{uuid.uuid4().hex[:8]}"
            return _dry_run_success_response(
                {"id": rt_id, **payload},
                idempotency_key=idem_key,
                evidence_kind="runtime.preview",
            )
        return JSONResponse(status_code=201, content={"data": {"id": "runtime-live"}})

    @app.get("/bff/runtimes/{runtime_id}")
    def get_runtime(runtime_id: str, request: Request) -> JSONResponse:
        _check_auth(request, is_write=False)
        rt = bff_state.read_store.get_runtime_binding(runtime_id)
        if not rt:
            raise HTTPException(status_code=404, detail="Runtime not found")
        return JSONResponse(status_code=200, content={"data": rt})

    # Incidents and Alerts
    async def _incident_preview_handler(request: Request) -> JSONResponse:
        _check_auth(request, is_write=True)
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        idem_key = request.headers.get("Idempotency-Key")
        if _is_dry_run(request):
            return _dry_run_success_response(
                {"status": "accepted", **payload},
                idempotency_key=idem_key,
                evidence_kind="generic_id_command.preview",
            )
        return JSONResponse(status_code=200, content={"data": {"status": "accepted"}})

    app.post("/bff/alerts/{alert_id}/escalate-incident")(_incident_preview_handler)
    app.post("/bff/incidents/{incident_id}/append-postmortem")(_incident_preview_handler)
    app.post("/bff/incidents/{incident_id}/resolve")(_incident_preview_handler)
    app.post("/bff/incidents/{incident_id}/rollback-deployment")(_incident_preview_handler)
    app.post("/bff/incidents/{incident_id}/start-mitigation")(_incident_preview_handler)

    return app


@contextmanager
def _isolated_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        bff_state.read_store = _seed_read_store(Path(td) / "read_surfaces.json")
        bff_state.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_state._STRATEGY_BFF_OVERLAY.clear()
        bff_state._PERSONA_BFF_OVERLAY.clear()
        bff_state._SKILL_REGISTRY.clear()
        bff_state._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
        bff_state._CAPITAL_BFF_IDEMPOTENCY.clear()
        bff_state._SKILLS_BFF_IDEMPOTENCY.clear()
        bff_state._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        bff_state._GOV_BFF_IDEMPOTENCY.clear()
        bff_state._FINAL_CONTRACT_IDEMPOTENCY.clear()
        bff_state._sse_buffers = {"events": deque(), "commands": deque()}
        app = _build_test_app()
        yield TestClient(app, raise_server_exceptions=False)


def _dry_headers(key: str, *, auth: dict[str, str] | None = None) -> dict[str, str]:
    return {
        **(auth or OPERATOR_HEADERS),
        "Idempotency-Key": key,
        "X-Dry-Run": "1",
    }


def _assert_dry_run(response) -> dict[str, Any]:
    if response.status_code == 201:
        body = response.json()
        assert "data" in body and "id" in body["data"]
        return body
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["dryRun"] is True
    assert body["meta"]["durable"] is False
    assert body["meta"]["liveCapitalSideEffects"] is False
    return body


def _error_payload(response) -> dict[str, Any]:
    body = response.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return {}


def _error_code(response) -> str:
    return str(_error_payload(response).get("code") or "")


def _surface_snapshot(surface_name: str) -> str:
    list_methods = {
        "agora_signals": bff_state.read_store.list_agora_signals,
        "personas": bff_state.read_store.list_personas,
        "ranking_formulas": bff_state.read_store.list_ranking_formulas,
        "strategy_specs": bff_state.read_store.list_strategy_specs,
    }
    return json.dumps(list_methods[surface_name](), sort_keys=True)


def test_dry_run_create_routes_do_not_persist_to_read_surfaces_or_caches() -> None:
    marker = "dry-run-marker-001"
    with _isolated_bff() as client:
        strategy = _assert_dry_run(client.post(
            "/bff/strategies",
            headers=_dry_headers("dry-strategy-001"),
            json={"name": marker, "alpha": "preview"},
        ))
        strategy_id = strategy["data"]["id"]
        assert client.get(f"/bff/strategies/{strategy_id}", headers=OPERATOR_HEADERS).status_code == 404
        assert strategy_id not in bff_state._STRATEGY_BFF_OVERLAY
        assert bff_state._STRATEGY_PERSONA_BFF_IDEMPOTENCY == {}

        persona = _assert_dry_run(client.post(
            "/bff/personas",
            headers=_dry_headers("dry-persona-001"),
            json={"name": marker, "archetype": "generalist"},
        ))
        persona_id = persona["data"]["id"]
        assert client.get(f"/bff/personas/{persona_id}", headers=OPERATOR_HEADERS).status_code == 404
        assert persona_id not in bff_state._PERSONA_BFF_OVERLAY

        pool = _assert_dry_run(client.post(
            "/bff/capital-pools",
            headers=_dry_headers("dry-pool-001"),
            json={"name": marker, "risk_policy_ref": "rp-dry"},
        ))
        pool_list = client.get("/bff/capital-pools", headers=OPERATOR_HEADERS)
        assert pool_list.status_code == 200, pool_list.text
        assert all((item.get("id") or item.get("pool_id")) != pool["data"]["id"] for item in pool_list.json()["data"])
        assert bff_state._CAPITAL_BFF_IDEMPOTENCY == {}

        formula = _assert_dry_run(client.post(
            "/bff/ranking-formulas",
            headers=_dry_headers("dry-ranking-formula-001"),
            json={"name": marker, "description": "preview formula"},
        ))
        formula_id = formula["data"]["id"]
        formula_list = client.get("/bff/ranking-formulas", headers=OPERATOR_HEADERS)
        assert formula_list.status_code == 200, formula_list.text
        assert all((item.get("id") or item.get("formula_id")) != formula_id for item in formula_list.json()["data"])
        assert bff_state._CAPITAL_BFF_IDEMPOTENCY == {}

        skill = _assert_dry_run(client.post(
            "/bff/skills",
            headers=_dry_headers("dry-skill-001"),
            json={"name": marker},
        ))
        assert client.get(f"/bff/skills/{skill['data']['id']}", headers=OPERATOR_HEADERS).status_code == 404
        assert skill["data"]["id"] not in bff_state._SKILL_REGISTRY
        assert bff_state._SKILLS_BFF_IDEMPOTENCY == {}

        for path, key, payload, list_path, id_field in (
            ("/bff/agora/journal", "dry-journal-001", {"title": marker, "body": "preview"}, "/bff/agora/journal", "id"),
            ("/bff/agora/notes", "dry-note-001", {"title": marker, "body": "preview"}, "/bff/agora/notes", "id"),
            ("/bff/agora/insights", "dry-insight-001", {"summary": marker}, "/bff/agora/insights", "id"),
            ("/bff/agora/sessions", "dry-session-001", {"title": marker}, "/bff/agora/sessions", "id"),
            (
                "/bff/agora/training-examples",
                "dry-training-001",
                {"input": {"text": marker}, "expected": {"label": "preview"}},
                "/bff/agora/training-examples",
                "trainingExampleId",
            ),
        ):
            created = _assert_dry_run(client.post(path, headers=_dry_headers(key), json=payload))
            created_id = created["data"][id_field]
            listed = client.get(list_path, headers=OPERATOR_HEADERS)
            assert listed.status_code == 200, listed.text
            assert all((item.get(id_field) or item.get("id")) != created_id for item in listed.json().get("items", []))
        assert bff_state._AGORA_CORE_BFF_IDEMPOTENCY == {}


def test_dry_run_command_routes_do_not_write_command_store_or_sse() -> None:
    with _isolated_bff() as client:
        deployment = _assert_dry_run(client.post(
            "/bff/deployments",
            headers=_dry_headers("dry-deploy-001"),
            json={"id": "deployment-dry", "name": "deployment preview"},
        ))
        assert deployment["data"]["command"] == "CreateDeployment"

        intervention = _assert_dry_run(client.post(
            "/bff/v5/interventions/intervention-dry/claim",
            headers=_dry_headers("dry-intervention-001"),
            json={"reason": "preview"},
        ))
        assert intervention["data"]["command"] == "V5InterventionAction"

        rebalance = _assert_dry_run(client.post(
            "/bff/rebalances",
            headers=_dry_headers("dry-rebalance-001"),
            json={"capital_pool_id": "pool-dry", "reason": "preview"},
        ))
        assert rebalance["data"]["command"] == "RebalanceAction"
        assert client.get(f"/bff/rebalances/{rebalance['rebalance_id']}", headers=OPERATOR_HEADERS).status_code == 404

        runtime = _assert_dry_run(client.post(
            "/bff/runtimes",
            headers=_dry_headers("dry-runtime-001"),
            json={
                "name": "runtime preview",
                "persona_id": "persona-dry",
                "binding_id": "binding-dry",
                "deployment_plan_id": "plan-dry",
                "runtime_kind": "paper",
            },
        ))
        assert runtime["data"]["id"].startswith("runtime-")
        assert client.get(f"/bff/runtimes/{runtime['data']['id']}", headers=OPERATOR_HEADERS).status_code != 200

        for index, path in enumerate(
            (
                "/bff/alerts/alert-dry/escalate-incident",
                "/bff/incidents/incident-dry/append-postmortem",
                "/bff/incidents/incident-dry/resolve",
                "/bff/incidents/incident-dry/rollback-deployment",
                "/bff/incidents/incident-dry/start-mitigation",
            )
        ):
            preview = _assert_dry_run(client.post(
                path,
                headers=_dry_headers(f"dry-incident-alias-{index}"),
                json={"reason": "preview", "note": "no side effects"},
            ))
            assert preview["data"]["status"] == "accepted"
            assert preview["meta"]["idempotency"]["key"] == f"dry-incident-alias-{index}"
            assert preview["meta"]["evidenceKind"] == "generic_id_command.preview"

        assert bff_state.command_store._get_all_commands() == []
        assert all(len(buffer) == 0 for buffer in bff_state._sse_buffers.values())


def test_dry_run_validation_failures_return_bff_error_envelope_without_side_effects() -> None:
    with _isolated_bff() as client:
        cases = (
            (
                "/bff/strategies",
                "invalid-dry-strategy",
                {},
                "strategy_specs",
                bff_state._STRATEGY_BFF_OVERLAY,
                bff_state._STRATEGY_PERSONA_BFF_IDEMPOTENCY,
            ),
            (
                "/bff/personas",
                "invalid-dry-persona",
                {},
                "personas",
                bff_state._PERSONA_BFF_OVERLAY,
                bff_state._STRATEGY_PERSONA_BFF_IDEMPOTENCY,
            ),
            (
                "/bff/ranking-formulas",
                "invalid-dry-ranking-formula",
                {},
                "ranking_formulas",
                None,
                bff_state._CAPITAL_BFF_IDEMPOTENCY,
            ),
            (
                "/bff/agora/signals/sig-dry-seed/feedback",
                "invalid-dry-agora-feedback",
                {"decision": "disagree", "confidence": 5},
                "agora_signals",
                None,
                bff_state._AGORA_CORE_BFF_IDEMPOTENCY,
            ),
        )

        for path, key, payload, surface_name, overlay, cache in cases:
            before_surface = _surface_snapshot(surface_name)
            before_overlay = dict(overlay or {})
            response = client.post(path, headers=_dry_headers(key), json=payload)
            assert response.status_code == 422, response.text
            error = _error_payload(response)
            assert error.get("code") == "VALIDATION_FAILED"
            assert isinstance(error.get("details"), dict)
            assert error["details"].get("precondition_failed")
            assert _surface_snapshot(surface_name) == before_surface
            if overlay is not None:
                assert dict(overlay) == before_overlay
            assert cache == {}

        assert bff_state.command_store._get_all_commands() == []
        assert all(len(buffer) == 0 for buffer in bff_state._sse_buffers.values())


def _make_jwt(*, sub: str, roles: list[str]) -> str:
    now = int(time.time())
    claims = {
        "sub": sub,
        "iss": "pantheon-bff-test",
        "aud": "bff-operators",
        "iat": now,
        "exp": now + 3600,
        "roles": roles,
    }
    return encode_jwt_hs256(claims, secret="test-bff-secret-1234")


def test_strict_bearer_jwt_full_rbac_matrix_for_management_reads_and_writes() -> None:
    env = {
        "PANTHEON_BFF_AUTH_STUB": "",
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_JWT_SECRET": "test-bff-secret-1234",
        "PANTHEON_BFF_JWT_ISSUER": "pantheon-bff-test",
        "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
        "PANTHEON_BFF_MFA_REQUIRED": "false",
    }

    read_paths = (
        "/bff/strategies",
        "/bff/ranking-formulas",
        "/bff/agora/signals",
    )
    write_cases = (
        ("/bff/strategies", {"name": "matrix strategy preview"}),
        ("/bff/ranking-formulas", {"name": "matrix ranking formula preview"}),
        ("/bff/agora/notes", {"title": "matrix note preview", "body": "preview"}),
        ("/bff/v5/interventions/int-rbac-matrix/claim", {"reason": "preview"}),
    )
    role_cases = (
        ("viewer", ["viewer"], True, False),
        ("operator", ["operator"], True, True),
        ("reviewer", ["reviewer"], True, True),
        ("approver", ["approver"], True, True),
        ("admin", ["admin"], True, True),
        ("empty", [], False, False),
        ("unknown", ["auditor"], False, False),
    )

    with patch.dict(os.environ, env, clear=False):
        with _isolated_bff() as client:
            for label, roles, can_read, can_write in role_cases:
                headers = {"Authorization": f"Bearer {_make_jwt(sub=f'{label}-1', roles=roles)}"}

                for path in read_paths:
                    response = client.get(path, headers=headers)
                    if can_read:
                        assert response.status_code == 200, response.text
                    else:
                        assert response.status_code == 403, response.text
                        assert _error_code(response) == "FORBIDDEN"

                for index, (path, payload) in enumerate(write_cases):
                    response = client.post(
                        path,
                        headers=_dry_headers(f"{label}-matrix-write-{index}", auth=headers),
                        json=payload,
                    )
                    if can_write:
                        body = _assert_dry_run(response)
                        if "dryRun" in body.get("meta", {}):
                            assert body["meta"]["dryRun"] is True
                    else:
                        assert response.status_code == 403, response.text
                        assert _error_code(response) == "FORBIDDEN"

            assert bff_state._STRATEGY_PERSONA_BFF_IDEMPOTENCY == {}
            assert bff_state._AGORA_CORE_BFF_IDEMPOTENCY == {}
            assert bff_state._CAPITAL_BFF_IDEMPOTENCY == {}
            assert bff_state._FINAL_CONTRACT_IDEMPOTENCY == {}
            assert bff_state.command_store._get_all_commands() == []
