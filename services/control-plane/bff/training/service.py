"""Session management support for the Training BFF domain.

The service keeps trainer-session validation, read-store access, and the
trainer-to-strategy-seed bridge behind explicit dependencies. It deliberately
does not import the BFF composition root.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from models import ErrorCode
from services.source_ingestion.trainer_seed_bridge import (
    TrainerSeedBridge,
    TrainerSeedBridgeError,
    trainer_seed_kind_from_text,
)

log = logging.getLogger(__name__)


class TrainingSessionService:
    """Shared session-management dependencies and validations for Training."""

    session_statuses = frozenset({"active", "paused", "completed", "abandoned"})
    replay_terminal_statuses = frozenset({"completed", "abandoned"})
    rapid_eval_scopes = frozenset(
        {"persona_patch", "strategy_patch", "feature_patch", "risk_patch"}
    )
    rapid_eval_active_statuses = frozenset({"active", "paused"})

    def __init__(
        self,
        *,
        get_read_store: Callable[[], Any],
        bff_error: Callable[..., Exception],
        utc_now: Callable[[], str],
        dataset_surface_status: Callable[..., Dict[str, Any]],
    ) -> None:
        self._get_read_store = get_read_store
        self._bff_error = bff_error
        self._utc_now = utc_now
        self._dataset_surface_status = dataset_surface_status

    @property
    def read_store(self) -> Any:
        """Resolve the current store per request, including test substitutions."""
        return self._get_read_store()

    def required_text(self, payload: Dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if value is None or not str(value).strip():
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Missing required field: {field}",
                f"{field} must be a non-empty string",
                precondition_failed=field,
            )
        return str(value).strip()

    def validate_session_status(self, value: Optional[str]) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in self.session_statuses:
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid trainer session status",
                f"status must be one of {sorted(self.session_statuses)}",
                precondition_failed="status",
            )
        return normalized

    def validate_context_refs(self, value: Any) -> List[Dict[str, str]]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid context_refs",
                "context_refs must be an array of { type, id } objects",
                precondition_failed="context_refs",
            )
        refs: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                raise self._bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid context_refs entry",
                    "Each context_refs entry must be an object",
                    precondition_failed="context_refs",
                )
            refs.append(
                {
                    "type": self.required_text(item, "type"),
                    "id": self.required_text(item, "id"),
                }
            )
        return refs

    def trainer_dialog_surface_state(
        self, *, snapshot_at: str, has_data: Optional[bool] = None
    ) -> str:
        surface = self._dataset_surface_status(
            "teaching_sessions",
            snapshot_at=snapshot_at,
            has_data=has_data,
        )
        if surface.get("status") == "unavailable":
            return "unavailable"
        if surface.get("source") == "local_snapshot":
            return "degraded"
        if surface.get("status") == "degraded":
            return "stale"
        return "fresh"

    def validate_refresh_mode(self, payload: Dict[str, Any]) -> str:
        refresh_mode = str(payload.get("refresh_mode") or "").strip().lower()
        mode = str(payload.get("mode") or "").strip().lower()
        if refresh_mode and refresh_mode != "manual":
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid trainer preview refresh mode",
                "refresh_mode must equal 'manual' or mode must equal 'refresh'",
                precondition_failed="refresh_mode",
            )
        if mode and mode != "refresh":
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid trainer preview refresh mode",
                "refresh_mode must equal 'manual' or mode must equal 'refresh'",
                precondition_failed="mode",
            )
        if refresh_mode == "manual":
            return refresh_mode
        if mode == "refresh":
            return mode
        raise self._bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid trainer preview refresh mode",
            "refresh_mode must equal 'manual' or mode must equal 'refresh'",
            precondition_failed="refresh_mode",
        )

    def validate_patch_payload(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        unknown_fields = sorted(set(payload.keys()) - {"patches"})
        if unknown_fields:
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid trainer control patch payload",
                f"Unsupported top-level fields: {unknown_fields}",
                precondition_failed="payload_shape",
            )

        patches = payload.get("patches")
        if not isinstance(patches, list) or not patches:
            raise self._bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid trainer control patch payload",
                "patches must be a non-empty array of { parameter_key, proposed_value } objects",
                precondition_failed="patches",
            )

        normalized: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        for index, patch in enumerate(patches):
            if not isinstance(patch, dict):
                raise self._bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid trainer control patch entry",
                    "Each patches[] entry must be an object",
                    precondition_failed=f"patches[{index}]",
                )
            unknown_patch_fields = sorted(
                set(patch.keys()) - {"parameter_key", "proposed_value"}
            )
            if unknown_patch_fields:
                raise self._bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid trainer control patch entry",
                    f"Unsupported patch fields: {unknown_patch_fields}",
                    precondition_failed=f"patches[{index}]",
                )
            parameter_key = str(patch.get("parameter_key") or "").strip()
            if not parameter_key:
                raise self._bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid trainer control patch entry",
                    "parameter_key must be a non-empty string",
                    precondition_failed=f"patches[{index}].parameter_key",
                )
            if parameter_key in seen_keys:
                raise self._bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid trainer control patch payload",
                    f"Duplicate parameter_key is not allowed: {parameter_key}",
                    precondition_failed=f"patches[{index}].parameter_key",
                )
            normalized.append(
                {
                    "parameter_key": parameter_key,
                    "proposed_value": patch.get("proposed_value"),
                }
            )
            seen_keys.add(parameter_key)
        return normalized

    @staticmethod
    def candidate_snapshot_at(replay: Dict[str, Any]) -> Optional[str]:
        preview_events = sorted(
            [
                event
                for event in (replay.get("events") or [])
                if isinstance(event, dict)
                and event.get("event_type") == "preview_trigger"
            ],
            key=lambda event: int(event.get("sequence_number") or 0),
        )
        if not preview_events:
            return None
        return (preview_events[-1].get("eval_ref") or {}).get(
            "candidate_snapshot_at"
        )

    @staticmethod
    def _trainer_seed_summary(
        *, replay: Dict[str, Any], commit_result: Dict[str, Any]
    ) -> str:
        parts: List[str] = []
        objective = str(replay.get("objective") or "").strip()
        if objective:
            parts.append(objective)
        for event in replay.get("events") or []:
            if not isinstance(event, dict):
                continue
            if str(event.get("event_type") or "").strip().lower() == "message":
                continue
            summary = str(event.get("summary") or "").strip()
            if summary and summary not in parts:
                parts.append(summary)
        commit_event = commit_result.get("event") or {}
        if isinstance(commit_event, dict):
            summary = str(commit_event.get("summary") or "").strip()
            if summary and summary not in parts:
                parts.append(summary)
        return " ".join(parts)

    @staticmethod
    def _trainer_seed_artifact_refs(
        commit_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        refs = dict(commit_result.get("artifacts") or {})
        event = commit_result.get("event") or {}
        if isinstance(event, dict) and isinstance(event.get("artifact_refs"), dict):
            refs.update({key: value for key, value in event["artifact_refs"].items() if value})
        return refs

    def trainer_seed_extraction_response(
        self,
        *,
        replay: Dict[str, Any],
        commit_result: Dict[str, Any],
        request_payload: Dict[str, Any],
        identity: Any,
    ) -> Dict[str, Any]:
        summary = self._trainer_seed_summary(
            replay=replay, commit_result=commit_result
        )
        raw_kind = str(
            request_payload.get("seed_kind")
            or request_payload.get("seedKind")
            or ""
        ).strip()
        commit_event = commit_result.get("event") or {}
        event_id = str(
            commit_event.get("event_id")
            or f"{commit_result.get('session_id')}-commit"
        ).strip()
        session_id = str(
            commit_result.get("session_id") or replay.get("session_id") or ""
        ).strip()
        bridge_event = {
            "event_type": "trainer_commit",
            "event_id": event_id,
            "session_id": session_id,
            "persona_id": replay.get("persona_id"),
            "summary": summary,
            "seed_kind": raw_kind or trainer_seed_kind_from_text(summary).value,
            "committed_by": commit_result.get("committed_by") or identity.operator_id,
            "committed_at": commit_result.get("committed_at") or self._utc_now(),
            "raw_ref": f"evidence://trainer/{session_id}/{event_id}",
            "artifact_refs": self._trainer_seed_artifact_refs(commit_result),
            "strategy_seed": {
                "hypothesis": summary,
                "asset_class": ["unspecified"],
                "market_scope": ["unspecified"],
                "required_data": ["governed trainer commit evidence"],
                "risk_notes": ["trainer_seed_requires_review"],
            },
        }
        try:
            result = TrainerSeedBridge(created_by=identity.operator_id).ingest_event(
                bridge_event,
                requested_by=identity.operator_id,
            )
        except TrainerSeedBridgeError as exc:
            log.info("Trainer seed bridge refused committed event: %s", exc)
            return {
                "status": "refused",
                "code": exc.code,
                "message": str(exc),
                "research_only": True,
                "execution_route": "none",
            }
        except Exception as exc:  # pragma: no cover - defensive BFF degradation path.
            log.exception("Trainer seed bridge unavailable for committed event: %s", exc)
            return {
                "status": "unavailable",
                "code": "trainer_seed_bridge_unavailable",
                "message": "Trainer seed extraction is temporarily unavailable.",
                "research_only": True,
                "execution_route": "none",
            }
        return {
            "status": "created" if result.was_created else "existing",
            "seed_id": result.seed.seed_id,
            "seed_kind": result.seed.metadata.get("seed_kind"),
            "interaction_id": result.interaction_record.interaction_id,
            "intent": result.classification.primary_intent.value,
            "requires_human_review": result.classification.requires_human_review,
            "trainer_seed_extraction_ref": result.extraction_ref.to_dict(),
            "redaction_findings": list(result.redaction_findings),
            "review_inbox": {
                "status": result.seed.status.value,
                "route": "/bff/management/strategy-seeds",
            },
            "research_only": True,
            "execution_route": "none",
        }
