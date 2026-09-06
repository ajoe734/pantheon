"""HTTP facade for the canonical memory stores."""
from __future__ import annotations

import os
import json
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response, Header
from pydantic import BaseModel

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture

from .institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryError,
    InstitutionalMemoryStore,
    build_institutional_memory_store,
)
from .learn_feedback_writeback import (
    LearnFeedbackUnauthorizedError,
    LearnFeedbackWritebackError,
    write_learn_feedback,
)
from .persona_memory_store import (
    PersonaMemoryEntry,
    PersonaMemoryError,
    PersonaMemoryStore,
    PersonaRelevanceScope,
    build_persona_memory_store,
)
from services.persona.lesson_governance import (
    TradeLessonCandidateStore,
    LessonGovernanceService,
    TradeLessonCandidateError,
    is_sensitive_change,
)
from .search_retrieval import (
    get_search_retrieval_backend,
    project_institutional_entry,
    project_persona_entry,
    retrieve_institutional_with_backend,
    retrieve_persona_with_backend,
)


def _split_csv_values(values: Optional[List[str]]) -> List[str]:
    normalized: List[str] = []
    for value in values or []:
        normalized.extend(part.strip() for part in str(value).split(",") if part.strip())
    return normalized

app = FastAPI(title="Pantheon Memory Service", version="0.1.0")
STORE_BACKEND = os.getenv("PANTHEON_MEMORY_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("memory")
register_fastapi_health_routes(
    app,
    "memory",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    details=lambda: {
        "institutional_store_path": str(_store_path()),
        "persona_store_path": str(_persona_store_path()),
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)


def _store_path() -> Path:
    explicit = os.getenv("PANTHEON_MEMORY_STORE", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = Path(os.getenv("PANTHEON_MEMORY_DATA_DIR", "/tmp/pantheon/memory"))
    return data_dir / "institutional_memory_entries.json"


def _persona_store_path() -> Path:
    explicit = os.getenv("PANTHEON_PERSONA_MEMORY_STORE", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = Path(os.getenv("PANTHEON_MEMORY_DATA_DIR", "/tmp/pantheon/memory"))
    return data_dir / "persona_memory_entries.json"


def _store() -> InstitutionalMemoryStore:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_institutional_memory_store(path)


def _persona_store() -> PersonaMemoryStore:
    path = _persona_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_persona_memory_store(path)


def _candidate_store_path() -> Path:
    explicit = os.getenv("PANTHEON_TRADE_LESSON_CANDIDATE_STORE", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = Path(os.getenv("PANTHEON_MEMORY_DATA_DIR", "/tmp/pantheon/memory"))
    return data_dir / "trade_lesson_candidates.json"


def _candidate_store() -> TradeLessonCandidateStore:
    path = _candidate_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return TradeLessonCandidateStore(path)


def _governance_service() -> LessonGovernanceService:
    return LessonGovernanceService(_candidate_store())


def _fetch_governance_approval(decision_id: str) -> Optional[Dict[str, Any]]:
    # In local mode, read the json file directly
    if os.getenv("PANTHEON_MEMORY_AUTHZ_MODE", "").strip().lower() == "local":
        gov_dir = os.getenv("PANTHEON_GOVERNANCE_DATA_DIR") or "/tmp/pantheon/governance"
        store_path = Path(gov_dir) / "approval_decisions.json"
        if not store_path.exists():
            workspace_path = Path(__file__).resolve().parents[2] / "services" / "governance" / "data" / "approval_decisions.json"
            if workspace_path.exists():
                store_path = workspace_path
        if store_path.exists():
            try:
                decisions = json.loads(store_path.read_text(encoding="utf-8"))
                for d in decisions:
                    if d.get("decision_id") == decision_id:
                        return d
            except Exception:
                pass
        return None

    # In live mode, query the governance API
    base = (
        os.getenv("PANTHEON_GOVERNANCE_API_URL")
        or os.getenv("PANTHEON_GOVERNANCE_SERVICE_URL")
        or ""
    ).strip().rstrip("/")
    if not base:
        return None
    url = f"{base}/api/governance/approvals/{decision_id}"
    headers = {}
    token = os.getenv("PANTHEON_GOVERNANCE_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        return None


def _authorize_lesson_action(
    actor_id: Optional[str],
    actor_roles: Optional[List[str]],
    action: str = "lesson.decide",
) -> None:
    if not actor_id or not actor_id.strip():
        raise HTTPException(
            status_code=403,
            detail={"error": "unauthorized", "message": "Missing actor ID."}
        )

    request_payload = {
        "action": action,
        "actor_id": actor_id,
        "actor_roles": actor_roles or [],
        "resource": {},
        "context": {},
    }

    if os.getenv("PANTHEON_MEMORY_AUTHZ_MODE", "").strip().lower() == "local":
        from services.governance.authz import evaluate_authz_request
        decision = evaluate_authz_request(**request_payload)
    else:
        url = _governance_authz_url()
        if not url:
            raise HTTPException(
                status_code=403,
                detail={"error": "unauthorized", "message": "Governance authz unconfigured."}
            )
        body = json.dumps(request_payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = os.getenv("PANTHEON_GOVERNANCE_AUTH_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=float(os.getenv("PANTHEON_MEMORY_AUTHZ_TIMEOUT", "2"))) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
            decision = {"allowed": bool(payload.get("allowed")), "reason": str(payload.get("reason") or "unknown")}
        except Exception:
            raise HTTPException(
                status_code=403,
                detail={"error": "unauthorized", "message": "Governance authz service unavailable."}
            )

    if not decision.get("allowed"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "unauthorized",
                "message": f"Actor roles {actor_roles} not authorized for governance action. Reason: {decision.get('reason')}"
            }
        )


def _governance_authz_url() -> str:
    base = (
        os.getenv("PANTHEON_GOVERNANCE_AUTHZ_URL")
        or os.getenv("PANTHEON_GOVERNANCE_API_URL")
        or os.getenv("PANTHEON_GOVERNANCE_SERVICE_URL")
        or ""
    ).strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/api/governance/authz/check"


def _authorize_memory_retrieve(
    *,
    actor_id: str,
    actor_roles: List[str],
    resource: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    request_payload = {
        "action": "memory.retrieve",
        "actor_id": actor_id,
        "actor_roles": actor_roles,
        "resource": resource,
        "context": context,
    }
    if os.getenv("PANTHEON_MEMORY_AUTHZ_MODE", "").strip().lower() == "local":
        from services.governance.authz import evaluate_authz_request

        decision = evaluate_authz_request(**request_payload)
        return {"allowed": bool(decision.get("allowed")), "reason": str(decision.get("reason") or "unknown")}

    url = _governance_authz_url()
    if not url:
        return {"allowed": False, "reason": "governance_authz_unconfigured"}

    body = json.dumps(request_payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("PANTHEON_GOVERNANCE_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=float(os.getenv("PANTHEON_MEMORY_AUTHZ_TIMEOUT", "2"))) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (urllib.error.HTTPError, OSError, ValueError, json.JSONDecodeError):
        return {"allowed": False, "reason": "governance_authz_unavailable"}
    return {"allowed": bool(payload.get("allowed")), "reason": str(payload.get("reason") or "unknown")}


@app.get("/__health__")
async def health():
    return {"status": "ok", "service": "memory"}


@app.post("/api/memory/entries", status_code=201)
async def store_entry(payload: Dict[str, Any]):
    try:
        entry = InstitutionalMemoryEntry.from_dict(payload)
        saved = _store().create(entry)
    except (InstitutionalMemoryError, TypeError) as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_entry", "message": str(exc)}) from exc
    return {"entry_id": saved.entry_id}


def _store_persona_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    try:
        entry = PersonaMemoryEntry.from_dict(payload)
        saved = _persona_store().create(entry)
    except (PersonaMemoryError, TypeError) as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_persona_entry", "message": str(exc)}) from exc
    return {"memory_id": saved.memory_id}


@app.post("/api/memory/persona-entries", status_code=201)
async def store_persona_entry(payload: Dict[str, Any]):
    return _store_persona_payload(payload)


@app.post("/api/memory/writebacks/persona", status_code=201)
async def writeback_persona_entry(payload: Dict[str, Any]):
    return _store_persona_payload(payload)


@app.post("/api/memory/writebacks/learn-feedback", status_code=201)
async def writeback_learn_feedback(payload: Dict[str, Any], response: Response):
    try:
        result = write_learn_feedback(
            payload,
            persona_store=_persona_store(),
            institutional_store=_store(),
        )
    except LearnFeedbackUnauthorizedError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "learn_feedback_writeback_unauthorized", "message": str(exc)},
        ) from exc
    except (LearnFeedbackWritebackError, InstitutionalMemoryError, PersonaMemoryError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_learn_feedback_writeback", "message": str(exc)},
        ) from exc
    if not result.get("created"):
        response.status_code = 200
    return result


@app.get("/api/memory/entries")
async def list_entries(
    knowledge_type: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    scope_filter: Optional[str] = Query(default=None),
    contributing_persona_id: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
):
    entries = _store().list(
        knowledge_type=knowledge_type,
        scope=scope,
        scope_filter=scope_filter,
        contributing_persona_id=contributing_persona_id,
        active_only=active_only,
    )
    return {"entries": [entry.to_dict() for entry in entries], "count": len(entries)}


@app.get("/api/memory/entries/{entry_id}")
async def get_entry(entry_id: str):
    entry = _store().get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail={"error": "entry_not_found", "entry_id": entry_id})
    return entry.to_dict()


@app.get("/api/memory/retrieve")
async def retrieve_memory(
    actor_id: str = Query(..., min_length=1),
    actor_roles: List[str] = Query(...),
    session_id: str = Query(..., min_length=1),
    persona_id: Optional[str] = Query(default=None),
    session_persona_id: Optional[str] = Query(default=None),
    scope: str = Query(default="both", pattern="^(institutional|persona|both)$"),
    query: str = Query(default=""),
    knowledge_type: Optional[str] = Query(default=None),
    memory_type: Optional[str] = Query(default=None),
    scope_filter: Optional[str] = Query(default=None),
    tags: Optional[List[str]] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
):
    roles = _split_csv_values(actor_roles)
    tag_values = _split_csv_values(tags)
    resource = {"scope": scope}
    if persona_id:
        resource["persona_id"] = persona_id
    persona_relevance_scope = None
    if "consultation_session" in roles and scope in {"persona", "both"}:
        persona_relevance_scope = PersonaRelevanceScope.PERSONA_AND_COMMITTEE.value
        resource["relevance_scope"] = persona_relevance_scope
    context = {"session_id": session_id}
    if session_persona_id:
        context["session_persona_id"] = session_persona_id

    decision = _authorize_memory_retrieve(
        actor_id=actor_id,
        actor_roles=roles,
        resource=resource,
        context=context,
    )
    if not decision.get("allowed"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "memory_retrieve_unauthorized",
                "reason": str(decision.get("reason") or "unknown"),
            },
        )

    search_backend = get_search_retrieval_backend()
    ranked_hits = []
    if scope in {"institutional", "both"}:
        institutional_store = _store()
        hits = retrieve_institutional_with_backend(
            institutional_store,
            backend=search_backend,
            query=query,
            knowledge_type=knowledge_type,
            scope_filter=scope_filter,
            tags=tag_values,
            limit=limit,
        )
        for hit in hits:
            ranked_hits.append(
                ("institutional", hit.relevance_score, hit.entry.written_at, hit.entry.entry_id, hit.entry)
            )

    if scope in {"persona", "both"}:
        try:
            persona_store = _persona_store()
            hits = retrieve_persona_with_backend(
                persona_store,
                backend=search_backend,
                persona_id=persona_id or "",
                query=query,
                memory_type=memory_type,
                relevance_scope=persona_relevance_scope,
                tags=tag_values,
                limit=limit,
            )
            for hit in hits:
                ranked_hits.append(
                    ("persona", hit.relevance_score, hit.entry.written_at, hit.entry.memory_id, hit.entry)
                )
        except PersonaMemoryError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_persona_retrieve", "message": str(exc)},
            ) from exc

    ranked_hits.sort(key=lambda item: (item[1], item[2], item[3]), reverse=True)
    selected_hits = ranked_hits[:limit]
    response_hits = []
    institutional_store = None
    persona_store = None
    for hit_type, relevance_score, _written_at, _entry_id, entry in selected_hits:
        if hit_type == "institutional":
            if institutional_store is None:
                institutional_store = _store()
            updated = institutional_store.mark_reused(entry.entry_id)
        else:
            if persona_store is None:
                persona_store = _persona_store()
            updated = persona_store.mark_reused(entry.memory_id)
        response_hits.append(
            {
                "type": hit_type,
                "relevance_score": relevance_score,
                "entry": updated.to_dict(),
            }
        )

    return {
        "hits": response_hits,
        "count": len(response_hits),
        "scope": scope,
        "authz": {
            "allowed": True,
            "reason": str(decision.get("reason") or "authorized"),
            "policy_version": "governance-authz.v1",
        },
    }


@app.post("/api/memory/trade-lessons", status_code=201)
async def create_trade_lesson(payload: Dict[str, Any]):
    try:
        saved = _candidate_store().create(payload)
    except TradeLessonCandidateError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_candidate", "message": str(exc)}) from exc
    return saved


@app.get("/api/memory/trade-lessons")
async def list_trade_lessons(
    persona_id: Optional[str] = Query(default=None),
    review_state: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
):
    candidates = _candidate_store().list(
        persona_id=persona_id,
        review_state=review_state,
        scope=scope,
    )
    return {"candidates": candidates, "count": len(candidates)}


@app.get("/api/memory/trade-lessons/{candidate_id}")
async def get_trade_lesson(candidate_id: str):
    candidate = _candidate_store().get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_not_found", "candidate_id": candidate_id})
    return candidate


@app.post("/api/memory/trade-lessons/{candidate_id}/submit-review")
async def submit_trade_lesson_review(candidate_id: str):
    try:
        updated = _governance_service().submit_review(candidate_id)
    except TradeLessonCandidateError as exc:
        raise HTTPException(status_code=422, detail={"error": "submit_review_failed", "message": str(exc)}) from exc
    return updated


class DecidePayload(BaseModel):
    action: str
    operator_id: str
    reason: str
    audit_receipt_id: str
    episodes: Optional[List[Dict[str, Any]]] = None
    actor_roles: Optional[List[str]] = None
    target_env: Optional[str] = None
    promotion_stage: Optional[str] = None


@app.post("/api/memory/trade-lessons/{candidate_id}/decide")
async def decide_trade_lesson(
    candidate_id: str,
    payload: DecidePayload,
    x_actor_id: Optional[str] = Header(None, alias="X-Actor-ID"),
    x_actor_roles: Optional[str] = Header(None, alias="X-Actor-Roles"),
):
    if not x_actor_id or not x_actor_id.strip():
        raise HTTPException(
            status_code=403,
            detail={"error": "unauthorized", "message": "Missing authenticated actor ID Header (X-Actor-ID)."}
        )
    actor_id = x_actor_id

    if not x_actor_roles or not x_actor_roles.strip():
        raise HTTPException(
            status_code=403,
            detail={"error": "unauthorized", "message": "Missing authenticated roles Header (X-Actor-Roles)."}
        )
    actor_roles = [r.strip() for r in x_actor_roles.split(",") if r.strip()]

    _authorize_lesson_action(actor_id, actor_roles, action="lesson.decide")

    candidate = _candidate_store().get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_not_found", "candidate_id": candidate_id})

    req_env = payload.target_env or candidate.get("target_env", "paper")
    is_sensitive = is_sensitive_change(candidate)

    if payload.action == "endorse" and (is_sensitive or req_env in {"canary", "live"}):
        decision = _fetch_governance_approval(payload.audit_receipt_id)
        if not decision:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision {payload.audit_receipt_id} not found."
                }
            )
        is_approved = (
            str(decision.get("decision")).lower() == "approved" or
            str(decision.get("decision_state")).lower() == "decided" and str(decision.get("decision")).lower() == "approved"
        )
        if not is_approved:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision {payload.audit_receipt_id} is not approved."
                }
            )
        if decision.get("persona_id") != candidate["persona_id"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision persona mismatch. Candidate: {candidate['persona_id']}, Decision: {decision.get('persona_id')}."
                }
            )
        target_id = decision.get("target_id")
        if not target_id or not str(target_id).strip():
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": "Governance decision target_id is missing or empty."
                }
            )
        if str(target_id).strip() != candidate["lesson_candidate_id"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision target_id mismatch. Candidate: {candidate['lesson_candidate_id']}, Decision: {target_id}."
                }
            )
        target_version = decision.get("target_version")
        if not target_version or not str(target_version).strip():
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": "Governance decision target_version is missing or empty."
                }
            )
        if str(target_version).strip() != candidate.get("reflection_version"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision target_version mismatch. Candidate: {candidate.get('reflection_version')}, Decision: {target_version}."
                }
            )

    try:
        updated = _governance_service().decide(
            candidate_id,
            action=payload.action,
            operator_id=actor_id,  # Bind authenticated principal to operator_id
            reason=payload.reason,
            audit_receipt_id=payload.audit_receipt_id,
            episodes=payload.episodes,
            target_env=payload.target_env,
            promotion_stage=payload.promotion_stage,
        )
    except TradeLessonCandidateError as exc:
        raise HTTPException(status_code=422, detail={"error": "decision_failed", "message": str(exc)}) from exc
    return updated


@app.post("/api/memory/trade-lessons/{candidate_id}/merge")
async def merge_trade_lesson(
    candidate_id: str,
    actor_id: Optional[str] = Query(None),
    actor_roles: Optional[List[str]] = Query(None),
    x_actor_id: Optional[str] = Header(None, alias="X-Actor-ID"),
    x_actor_roles: Optional[str] = Header(None, alias="X-Actor-Roles"),
):
    if not x_actor_id or not x_actor_id.strip():
        raise HTTPException(
            status_code=403,
            detail={"error": "unauthorized", "message": "Missing authenticated actor ID Header (X-Actor-ID)."}
        )
    effective_id = x_actor_id

    if not x_actor_roles or not x_actor_roles.strip():
        raise HTTPException(
            status_code=403,
            detail={"error": "unauthorized", "message": "Missing authenticated roles Header (X-Actor-Roles)."}
        )
    effective_roles = [r.strip() for r in x_actor_roles.split(",") if r.strip()]

    _authorize_lesson_action(effective_id, effective_roles, action="lesson.merge")

    candidate = _candidate_store().get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_not_found", "candidate_id": candidate_id})

    # Revalidate approved receipt at merge for sensitive changes or canary/live target_env
    is_sensitive = is_sensitive_change(candidate)
    req_env = candidate.get("target_env", "paper")
    if is_sensitive or req_env in {"canary", "live"}:
        receipt = candidate.get("receipt", {})
        audit_receipt_id = receipt.get("audit_receipt_id")
        if not audit_receipt_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": "Missing audit_receipt_id for sensitive change at merge."
                }
            )
        decision = _fetch_governance_approval(audit_receipt_id)
        if not decision:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision {audit_receipt_id} not found at merge."
                }
            )
        is_approved = (
            str(decision.get("decision")).lower() == "approved" or
            str(decision.get("decision_state")).lower() == "decided" and str(decision.get("decision")).lower() == "approved"
        )
        if not is_approved:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision {audit_receipt_id} is not approved at merge."
                }
            )
        if decision.get("persona_id") != candidate["persona_id"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision persona mismatch at merge. Candidate: {candidate['persona_id']}, Decision: {decision.get('persona_id')}."
                }
            )
        target_id = decision.get("target_id")
        if not target_id or not str(target_id).strip():
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": "Governance decision target_id is missing or empty at merge."
                }
            )
        if str(target_id).strip() != candidate["lesson_candidate_id"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision target_id mismatch at merge. Candidate: {candidate['lesson_candidate_id']}, Decision: {target_id}."
                }
            )
        target_version = decision.get("target_version")
        if not target_version or not str(target_version).strip():
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": "Governance decision target_version is missing or empty at merge."
                }
            )
        if str(target_version).strip() != candidate.get("reflection_version"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "unauthorized",
                    "message": f"Governance decision target_version mismatch at merge. Candidate: {candidate.get('reflection_version')}, Decision: {target_version}."
                }
            )

    try:
        updated = _governance_service().merge_to_memory(candidate_id, _persona_store())
    except TradeLessonCandidateError as exc:
        raise HTTPException(status_code=422, detail={"error": "merge_failed", "message": str(exc)}) from exc
    return updated


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8086"))
    uvicorn.run("services.memory.main:app", host="0.0.0.0", port=port, reload=False)
