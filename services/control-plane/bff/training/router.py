"""Training domain router for trainer sessions, replay, and rapid evaluation."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, Query

from models import ErrorCode

from .service import TrainingSessionService


def create_training_router(
    *,
    get_read_store: Callable[[], Any],
    extract_identity: Callable[[Optional[str]], Any],
    require_read_role: Callable[[Any], None],
    bff_error: Callable[..., Exception],
    utc_now: Callable[[], str],
    page_slice: Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]],
    dataset_surface_status: Callable[..., Dict[str, Any]],
) -> APIRouter:
    """Build the dedicated Training domain router with injected BFF ports."""
    router = APIRouter()
    service = TrainingSessionService(
        get_read_store=get_read_store,
        bff_error=bff_error,
        utc_now=utc_now,
        dataset_surface_status=dataset_surface_status,
    )
    @router.post("/api/v1/trainer/sessions")
    async def create_trainer_session(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        persona_id = service.required_text(payload, "persona_id")
        session_type = service.required_text(payload, "session_type")
        objective = service.required_text(payload, "objective")
        context_refs = service.validate_context_refs(payload.get("context_refs"))

        if session_type != "trainer":
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid trainer session type",
                "session_type must equal 'trainer' for TW-01",
                precondition_failed="session_type",
            )

        persona = service.read_store.get_persona(persona_id)
        if not persona:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        session = service.read_store.create_trainer_session(
            persona_id=persona_id,
            objective=objective,
            context_refs=context_refs,
            actor_id=identity.operator_id,
            created_at=utc_now(),
        )
        if session is None:
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Trainer session store unavailable",
                "Trainer session creation store is unavailable.",
            )

        return {
            "session_id": session["session_id"],
            "persona_id": session["persona_id"],
            "session_type": session["session_type"],
            "objective": session["objective"],
            "status": session["status"],
            "started_at": session["started_at"],
            "allowedActions": session["allowedActions"],
            "links": session["links"],
        }


    @router.get("/api/v1/trainer/sessions")
    async def list_trainer_sessions(
        persona_id: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        # Enforce fail-closed ordering: authenticate before validating the required
        # persona_id query param, so an unauthenticated caller gets 401 (not 422) and
        # cannot probe endpoint existence/shape. Path-param siblings already do this.
        if not persona_id:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Request validation failed",
                "persona_id is required",
                precondition_failed="persona_id",
            )

        persona = service.read_store.get_persona(persona_id)
        if not persona:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        snapshot_at = utc_now()
        normalized_status = service.validate_session_status(status) if status is not None else None
        sessions = service.read_store.list_trainer_sessions(persona_id=persona_id, status=normalized_status) or []
        surface_state = service.trainer_dialog_surface_state(snapshot_at=snapshot_at, has_data=sessions is not None)

        total = len(sessions)
        if surface_state == "unavailable":
            page_items = []
            next_page_token = None
            total = 0
        else:
            page_items, next_page_token = page_slice(sessions, page_token, page_size)

        return {
            "data": page_items,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "trainer_dialog": surface_state,
                },
            },
        }


    @router.get("/api/v1/trainer/sessions/{session_id}")
    async def get_trainer_session_detail(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        session = service.read_store.get_trainer_session(session_id)
        if not session:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )

        snapshot_at = utc_now()
        payload = dict(session)
        payload["meta"] = {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "trainer_dialog": service.trainer_dialog_surface_state(snapshot_at=snapshot_at, has_data=True),
            },
        }
        return payload


    @router.get("/api/v1/trainer/sessions/{session_id}/controls")
    async def get_trainer_controls(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        controls = service.read_store.get_trainer_controls(session_id, snapshot_at=utc_now())
        if not controls:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )
        return controls


    @router.post("/api/v1/trainer/sessions/{session_id}/patch")
    async def patch_trainer_controls(
        session_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)
        patches = service.validate_patch_payload(payload)

        controls = service.read_store.get_trainer_controls(session_id, snapshot_at=utc_now())
        if not controls:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )
        if str(controls.get("status") or "").strip().lower() != "active":
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Trainer session cannot patch controls",
                "POST /patch is only allowed while the trainer session status is active",
                precondition_failed="status",
            )
        if not (controls.get("allowedActions") or {}).get("canPatchControls"):
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Trainer control patch unavailable",
                "allowedActions.canPatchControls is false for this trainer session",
                precondition_failed="allowedActions.canPatchControls",
            )

        result = service.read_store.patch_trainer_controls(
            session_id,
            patches=patches,
            patched_at=utc_now(),
        )
        if result is None:
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Trainer control store unavailable",
                "Trainer control patch store is unavailable.",
            )
        return result


    @router.post("/api/v1/trainer/sessions/{session_id}/message")
    async def append_trainer_message(
        session_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        session = service.read_store.get_trainer_session(session_id)
        if not session:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )
        if session["status"] != "active":
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Trainer session is not active",
                "POST /message is only allowed while the trainer session status is active",
                precondition_failed="status",
            )
        if not session["allowedActions"].get("canSendMessage"):
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Trainer message submission unavailable",
                "allowedActions.canSendMessage is false for this trainer session",
                precondition_failed="allowedActions.canSendMessage",
            )

        message_body = service.required_text(payload, "message_body")
        result = service.read_store.append_trainer_message(
            session_id,
            message_body=message_body,
            actor_id=identity.operator_id,
            accepted_at=utc_now(),
        )
        if result is None:
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Trainer session store unavailable",
                "Trainer message append store is unavailable.",
            )

        updated = result["session"]
        return {
            "session_id": updated["session_id"],
            "status": updated["status"],
            "accepted_at": result["accepted_at"],
            "event": result["event"],
            "session_summary": updated["session_summary"],
            "allowedActions": updated["allowedActions"],
        }


    @router.get("/api/v1/trainer/sessions/{session_id}/preview")
    async def get_trainer_preview(
        session_id: str,
        eval_id: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        session = service.read_store.get_trainer_session(session_id)
        if not session:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )

        snapshot_at = utc_now()
        preview = service.read_store.get_trainer_preview(
            session_id,
            session_status=session.get("status"),
            eval_id=eval_id,
            snapshot_at=snapshot_at,
        )
        if preview is None and eval_id:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer preview evaluation not found",
                f"Trainer preview evaluation {eval_id} does not exist for session {session_id}",
            )
        if preview is None:
            preview = service.read_store.build_trainer_preview_unavailable(
                session_id,
                session_status=session.get("status"),
                snapshot_at=snapshot_at,
            )
        return preview


    @router.post("/api/v1/trainer/sessions/{session_id}/preview")
    async def refresh_trainer_preview(
        session_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)
        service.validate_refresh_mode(payload)

        session = service.read_store.get_trainer_session(session_id)
        if not session:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )

        preview = service.read_store.get_trainer_preview(
            session_id,
            session_status=session.get("status"),
            snapshot_at=utc_now(),
        ) or service.read_store.build_trainer_preview_unavailable(
            session_id,
            session_status=session.get("status"),
            snapshot_at=utc_now(),
        )
        if session.get("status") not in {"active", "paused"}:
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Trainer session cannot refresh preview",
                "POST /preview is only allowed while the trainer session status is active or paused",
                precondition_failed="status",
            )
        if preview.get("status") == "pending":
            return preview
        if not preview.get("allowedActions", {}).get("canRefreshPreview"):
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Trainer preview refresh unavailable",
                "allowedActions.canRefreshPreview is false for this trainer preview",
                precondition_failed="allowedActions.canRefreshPreview",
            )

        refreshed = service.read_store.refresh_trainer_preview(
            session_id,
            session_status=session.get("status"),
            refreshed_at=utc_now(),
        )
        if refreshed is None:
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Trainer preview store unavailable",
                "Trainer preview refresh store is unavailable.",
            )
        return refreshed


    @router.get("/api/v1/trainer/replay")
    async def list_trainer_replays(
        persona_id: str,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        persona = service.read_store.get_persona(persona_id)
        if not persona:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        if status is not None:
            normalized_status = str(status).strip().lower()
            if normalized_status not in service.replay_terminal_statuses:
                raise bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid replay status filter",
                    f"status must be one of {sorted(service.replay_terminal_statuses)}",
                    precondition_failed="status",
                )
        else:
            normalized_status = None

        snapshot_at = utc_now()
        items, surface_state = service.read_store.list_trainer_replays(
            persona_id=persona_id,
            status=normalized_status,
            snapshot_at=snapshot_at,
        )
        total = len(items)
        page_items, next_page_token = page_slice(items, page_token, page_size)
        return {
            "data": page_items,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "trainer_replay": surface_state,
                },
            },
        }


    @router.get("/api/v1/trainer/replay/{session_id}")
    async def get_trainer_replay_detail(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        replay = service.read_store.get_trainer_replay(session_id, snapshot_at=snapshot_at)
        if not replay:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer replay session not found",
                f"Trainer replay session {session_id} does not exist",
            )
        return replay

    @router.post("/api/v1/trainer/sessions/{session_id}/commit")
    async def commit_trainer_replay(
        session_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        expected_candidate_snapshot_at = service.required_text(payload, "expected_candidate_snapshot_at")
        note = payload.get("note") or None

        replay = service.read_store.get_trainer_replay(session_id)
        if not replay:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer replay session not found",
                f"Trainer replay session {session_id} does not exist",
            )

        if str(replay.get("status") or "").strip().lower() != "completed":
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Trainer session cannot be committed",
                "commit is only allowed when session status is completed",
                precondition_failed="status",
            )

        if not replay.get("allowedActions", {}).get("canCommit"):
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Commit not allowed",
                "allowedActions.canCommit is false for this trainer replay session",
                precondition_failed="allowedActions.canCommit",
            )

        candidate_snapshot_at = service.candidate_snapshot_at(replay)
        if candidate_snapshot_at != expected_candidate_snapshot_at:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Candidate snapshot mismatch",
                "expected_candidate_snapshot_at does not match the current replayable candidate snapshot",
                precondition_failed="expected_candidate_snapshot_at",
            )

        result = service.read_store.commit_trainer_replay(
            session_id,
            expected_candidate_snapshot_at=expected_candidate_snapshot_at,
            note=note,
            actor_id=identity.operator_id,
            committed_at=utc_now(),
        )
        if result is None:
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Trainer replay store unavailable",
                "Trainer replay commit store is unavailable.",
            )
        result["seed_extraction"] = service.trainer_seed_extraction_response(
            replay=replay,
            commit_result=result,
            request_payload=payload,
            identity=identity,
        )
        return result


    @router.post("/api/v1/trainer/sessions/{session_id}/discard")
    async def discard_trainer_replay(
        session_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        expected_candidate_snapshot_at = service.required_text(payload, "expected_candidate_snapshot_at")
        note = payload.get("note") or None

        replay = service.read_store.get_trainer_replay(session_id)
        if not replay:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer replay session not found",
                f"Trainer replay session {session_id} does not exist",
            )

        if str(replay.get("status") or "").strip().lower() != "completed":
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Trainer session cannot be discarded",
                "discard is only allowed when session status is completed",
                precondition_failed="status",
            )

        if not replay.get("allowedActions", {}).get("canDiscard"):
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Discard not allowed",
                "allowedActions.canDiscard is false for this trainer replay session",
                precondition_failed="allowedActions.canDiscard",
            )

        candidate_snapshot_at = service.candidate_snapshot_at(replay)
        if candidate_snapshot_at != expected_candidate_snapshot_at:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Candidate snapshot mismatch",
                "expected_candidate_snapshot_at does not match the current replayable candidate snapshot",
                precondition_failed="expected_candidate_snapshot_at",
            )

        result = service.read_store.discard_trainer_replay(
            session_id,
            expected_candidate_snapshot_at=expected_candidate_snapshot_at,
            note=note,
            actor_id=identity.operator_id,
            discarded_at=utc_now(),
        )
        if result is None:
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Trainer replay store unavailable",
                "Trainer replay discard store is unavailable.",
            )
        return result


    @router.post("/api/v1/trainer/sessions/{session_id}/rapid-eval")
    async def create_rapid_eval(
        session_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        eval_scope = str(payload.get("eval_scope") or "").strip().lower()
        if not eval_scope or eval_scope not in service.rapid_eval_scopes:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid eval_scope",
                f"eval_scope must be one of {sorted(service.rapid_eval_scopes)}",
                precondition_failed="eval_scope",
            )

        dataset_version_id_raw = payload.get("dataset_version_id")
        if not dataset_version_id_raw or not str(dataset_version_id_raw).strip():
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Missing required field: dataset_version_id",
                "dataset_version_id must be a non-empty string",
                precondition_failed="dataset_version_id",
            )
        dataset_version_id = str(dataset_version_id_raw).strip()

        max_runtime_seconds_raw = payload.get("max_runtime_seconds")
        try:
            max_runtime_seconds = int(max_runtime_seconds_raw)
            if max_runtime_seconds <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid max_runtime_seconds",
                "max_runtime_seconds must be a positive integer",
                precondition_failed="max_runtime_seconds",
            )

        patch_ref = str(payload["patch_ref"]).strip() if payload.get("patch_ref") else None
        persona_id = str(payload["persona_id"]).strip() if payload.get("persona_id") else None
        strategy_id = str(payload["strategy_id"]).strip() if payload.get("strategy_id") else None

        session = service.read_store.get_trainer_session(session_id)
        if not session:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )

        if str(session.get("status") or "").strip().lower() not in service.rapid_eval_active_statuses:
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Trainer session cannot submit rapid eval",
                "rapid-eval is only allowed while the trainer session status is active or paused",
                precondition_failed="status",
            )

        result = service.read_store.create_rapid_eval(
            session_id,
            persona_id=persona_id,
            strategy_id=strategy_id,
            eval_scope=eval_scope,
            patch_ref=patch_ref,
            dataset_version_id=dataset_version_id,
            max_runtime_seconds=max_runtime_seconds,
            requested_by=identity.operator_id or "unknown",
            requested_at=utc_now(),
        )
        if result is None:
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Rapid eval store unavailable",
                "Rapid eval creation store is unavailable.",
            )
        return result


    @router.get("/api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}")
    async def get_rapid_eval(
        session_id: str,
        eval_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        session = service.read_store.get_trainer_session(session_id)
        if not session:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Trainer session not found",
                f"Trainer session {session_id} does not exist",
            )

        record = service.read_store.get_rapid_eval(eval_id, snapshot_at=utc_now())
        if not record or record.get("session_id") != session_id:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Rapid eval not found",
                f"Rapid eval {eval_id} does not exist for trainer session {session_id}",
            )
        return record

    return router
