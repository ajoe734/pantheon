"""Servant turn worker (AGORA-SERVANT-SESSION-DURABLE-20261127, AD-AG-05).

``POST /bff/agora/servant/sessions/{id}/messages`` only appends the trader's message to the
Agora owner and queues a turn. This worker (run by ``agora-interaction-worker``) claims the
queued turn, runs it with the last 20 messages of the session, appends the servant's answer
through the owner ``append_session_message`` and settles the turn. The trader reads the answer
over the existing ``/stream`` route.

AGORA-SERVANT-DATA-TOOLS-20261127: the turn may use the fixed read-only data tools
(``data_tools``) in at most 4 rounds; every number in the answer is a cited fact rendered here,
never a number the model wrote (``tool_turn``). The worker holds no order route and calls no
persona.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from services.agora.store import (
    SERVANT_HISTORY_MESSAGES,
    SERVANT_TURN_MAX_ATTEMPTS,
    ServantSessionMessageCapError,
)
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

from .clarification import (
    clarification_from_message,
    count_consecutive_clarifications,
    extract_clarification_habit_facts_from_messages,
)
from .data_tools import ServantDataTools, ToolContext, normalize_command_context
from .habits import HabitService, build_default_habit_service
from .tool_turn import SERVANT_TURN_SCHEMA, TurnOutcome, run_tool_turn

logger = logging.getLogger("agora.servant.turn_worker")

OPENCLAW_DEGRADED_CODE = "OPENCLAW_UPSTREAM_DEGRADED"
_WORKER_ROLES = ("persona",)
_SYSTEM_ROLES = ("system",)
# The lease must outlast the longest provider call; a crashed worker's turn is re-claimed after it.
DEFAULT_LEASE_SECONDS = 600
DEFAULT_PURGE_INTERVAL_SECONDS = 3600


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def provider_answer_from(data: Mapping[str, Any]) -> Optional[str]:
    for key in ("output", "answer"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    provider = data.get("provider")
    if isinstance(provider, dict):
        value = provider.get("answer")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def history_for_turn(
    messages: List[Mapping[str, Any]], user_message_id: str, limit: int = SERVANT_HISTORY_MESSAGES
) -> List[Dict[str, Any]]:
    """The last ``limit`` messages up to and including the turn's own user message."""
    upto = len(messages)
    for idx, message in enumerate(messages):
        if message.get("id") == user_message_id:
            upto = idx + 1
            break
    window = messages[max(0, upto - limit) : upto]
    history: List[Dict[str, Any]] = []
    for message in window:
        role = "assistant" if message.get("role") == "assistant" else "user"
        item: Dict[str, Any] = {"role": role, "content": str(message.get("content") or "")}
        meta = message.get("metadata") if isinstance(message.get("metadata"), Mapping) else {}
        refs = [
            a.get("ref") if isinstance(a, dict) else a for a in message.get("attachments") or []
        ]
        if role == "user":
            item["attachment_refs"] = [r for r in refs if r]
            if isinstance(meta.get("clarification_choice"), Mapping):
                item["clarification_choice"] = dict(meta["clarification_choice"])
            if isinstance(meta.get("habit_fact"), Mapping):
                item["habit_fact"] = dict(meta["habit_fact"])
        if role == "assistant":
            card = clarification_from_message(message)
            if card is not None:
                item["clarification"] = card
                item["answer_mode"] = "clarification"
            if not item["content"]:
                continue  # a degraded turn left no answer to replay
        history.append(item)
    return history


class ServantTurnWorker:
    """Claims queued servant turns from the Agora owner and runs each outside the request."""

    def __init__(
        self,
        *,
        owner: Any,
        client_factory: Optional[Callable[[], Any]] = None,
        worker_id: Optional[str] = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        purge_interval_seconds: float = DEFAULT_PURGE_INTERVAL_SECONDS,
        clock: Callable[[], str] = _utc_now,
        tools: Optional[ServantDataTools] = None,
        habits: Optional[HabitService] = None,
    ) -> None:
        self.owner = owner
        # SD §4.3: the fixed read-only tool set, resolved here and never by the model.
        self.tools = tools if tools is not None else ServantDataTools(agora_owner=owner)
        # SD §6: every turn carries the trader's habit profile and the servant's live (private)
        # interpretations, so a correction by the trader changes the next answer.
        self.habits = habits if habits is not None else build_default_habit_service(
            owner, journal_reader=getattr(self.tools, "journal_reader", None)
        )
        self.client_factory = client_factory or OpenClawOpsClient
        self.worker_id = worker_id or os.getenv(
            "PANTHEON_AGORA_SERVANT_WORKER_ID", f"servant-turn-worker-{uuid.uuid4().hex[:12]}"
        )
        self.lease_seconds = lease_seconds
        self.purge_interval_seconds = purge_interval_seconds
        self.clock = clock
        self._last_purge = 0.0
        self.metrics: Dict[str, int] = {"completed": 0, "degraded": 0, "failed": 0, "purged_sessions": 0}

    # -- one turn -----------------------------------------------------------------

    def _habit_context(self, ctx: ToolContext) -> Any:
        """Habit facts for the turn; a habit failure never blocks the trader's answer."""
        if self.habits is None:
            return None
        try:
            return self.habits.turn_context(
                tenant_id=ctx.tenant_id, user_id=ctx.user_id, persona_id=ctx.servant_persona_id,
                strategy_id=ctx.command_context.get("strategy_id"),
            )
        except Exception as exc:
            logger.warning("servant habit context unavailable: %s", type(exc).__name__)
            return None

    @staticmethod
    def _structured_round(client: Any, prompt: str, turn: Mapping[str, Any], audit: Mapping[str, Any]) -> Dict[str, Any]:
        """One structured provider call of the tool turn; returns the validated JSON object."""
        raw = client.invoke_structured_extraction(
            prompt=prompt,
            extraction_schema=SERVANT_TURN_SCHEMA,
            operator_id=str(turn.get("operator_id") or ""),
            trace_id=str(audit.get("trace_id") or "") or None,
        )
        data = raw.get("data") if isinstance(raw, dict) else None
        output = data.get("output") if isinstance(data, dict) else None
        structured = output.get("structured_data") if isinstance(output, dict) else None
        if not isinstance(structured, dict):
            raise OpenClawOpsClientError(
                "structured servant turn missing from provider response",
                status_code=502,
                error_code="SERVANT_TURN_SHAPE",
            )
        return structured

    def process_turn(self, turn: Dict[str, Any]) -> str:
        """Run one claimed turn and settle it. Returns the final status."""
        turn_id = str(turn["turn_id"])
        session_id = str(turn["session_id"])
        user_message_id = str(turn["user_message_id"])
        audit = dict(turn.get("audit") or {})
        persona_id = str(turn.get("servant_persona_id") or "")
        answer_id = f"servant-ans-{turn_id.removeprefix('servant-turn-')}"

        session = self.owner.get_servant_session(
            session_id,
            tenant_id=str(turn.get("tenant_id") or ""),
            user_id=str(turn.get("user_id") or ""),
        )
        if session is None:
            self.owner.finish_servant_turn(
                turn, status="failed", reason="session_missing", actor_roles=_WORKER_ROLES
            )
            self.metrics["failed"] += 1
            return "failed"
        if session.get("status") == "closed":
            self.owner.finish_servant_turn(
                turn, status="failed", reason="session_closed", actor_roles=_WORKER_ROLES
            )
            self.metrics["failed"] += 1
            return "failed"

        messages = [m for m in session.get("messages") or [] if isinstance(m, dict)]
        provider_status = "completed"
        answer: Optional[str] = None
        error: Optional[Dict[str, Any]] = None
        outcome: Optional[TurnOutcome] = None
        if int(turn.get("attempts") or 0) > SERVANT_TURN_MAX_ATTEMPTS:
            provider_status = "degraded"
            error = {"code": "SERVANT_TURN_ATTEMPTS_EXHAUSTED", "message": "worker did not finish the turn"}
        else:
            history = history_for_turn(messages, user_message_id)
            user_message = next((m for m in messages if m.get("id") == user_message_id), {})
            user_meta = user_message.get("metadata") if isinstance(user_message.get("metadata"), dict) else {}
            consecutive_clarifications, streak_cards = count_consecutive_clarifications(
                messages, before_message_id=user_message_id
            )
            cmd_ctx = normalize_command_context(user_meta.get("command_context"))
            ctx = ToolContext(
                tenant_id=str(turn.get("tenant_id") or ""),
                user_id=str(turn.get("user_id") or ""),
                operator_id=str(turn.get("operator_id") or ""),
                servant_persona_id=persona_id,
                command_context=cmd_ctx,
            )
            try:
                client = self.client_factory()
                outcome = run_tool_turn(
                    tools=self.tools,
                    ctx=ctx,
                    history=history,
                    invoke=lambda prompt: self._structured_round(client, prompt, turn, audit),
                    habit=self._habit_context(ctx),
                    consecutive_clarifications=consecutive_clarifications,
                    prior_clarifications=streak_cards,
                    clarification_choice=user_meta.get("clarification_choice")
                    if isinstance(user_meta.get("clarification_choice"), Mapping)
                    else None,
                    habit_facts=extract_clarification_habit_facts_from_messages(
                        messages, strategy_id=cmd_ctx.get("strategy_id")
                    ),
                    turn_id=turn_id,
                )
                answer = outcome.answer or None
                provider_status = outcome.provider_status
                error = outcome.error
            except Exception as exc:  # a poison turn must settle, not loop on the lease
                logger.exception("servant turn %s failed", turn_id)
                provider_status = "degraded"
                error = {"code": "SERVANT_TURN_INTERNAL", "message": type(exc).__name__}

        try:
            self.owner.append_session_message(
                session_id,
                answer_id,
                answer or "",
                persona_id,
                _WORKER_ROLES,
                payload={
                    "role": "assistant",
                    "sender": {"type": "servant", "id": persona_id},
                    "citations": list(outcome.facts) if outcome is not None else [],
                    "metadata": {
                        "turn_id": turn_id,
                        "reply_to": user_message_id,
                        "provider_status": provider_status,
                        "error": error,
                        "audit": audit,
                        **(
                            {"clarification": outcome.clarification}
                            if outcome is not None and outcome.clarification is not None
                            else {}
                        ),
                        **({"tool_turn": outcome.metadata()} if outcome is not None else {}),
                    },
                },
                created_at=self.clock(),
            )
        except ServantSessionMessageCapError:
            self.owner.finish_servant_turn(
                turn, status="failed", reason="message_cap", actor_roles=_WORKER_ROLES
            )
            self.metrics["failed"] += 1
            return "failed"
        final = "degraded" if provider_status == "degraded" else "completed"
        self.owner.finish_servant_turn(
            turn, status=final, answer_message_id=answer_id, actor_roles=_WORKER_ROLES
        )
        self.metrics[final] += 1
        return final

    # -- loop ---------------------------------------------------------------------

    def run_once(self, limit: int = 10) -> int:
        processed = 0
        while processed < limit:
            turn = self.owner.claim_servant_turn(
                lease_owner=self.worker_id,
                actor_roles=_WORKER_ROLES,
                lease_seconds=self.lease_seconds,
                now=self.clock(),
            )
            if turn is None:
                break
            self.process_turn(dict(turn))
            processed += 1
        self.maybe_purge()
        return processed

    def maybe_purge(self) -> Optional[Dict[str, int]]:
        """Cleanup path: servant sessions untouched for 180 days are deleted with their turns."""
        now = time.monotonic()
        if self._last_purge and now - self._last_purge < self.purge_interval_seconds:
            return None
        self._last_purge = now
        try:
            removed = self.owner.purge_expired_servant_sessions(
                actor_roles=_SYSTEM_ROLES, now=self.clock()
            )
        except Exception:
            logger.exception("servant session purge failed")
            return None
        self.metrics["purged_sessions"] += int(removed.get("sessions", 0))
        return removed
