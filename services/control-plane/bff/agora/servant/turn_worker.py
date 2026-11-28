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
    TraderReportDailyCapError,
)
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

from .clarification import (
    clarification_from_message,
    count_consecutive_clarifications,
    extract_clarification_habit_facts_from_messages,
)
from .data_tools import ServantDataTools, ToolContext, normalize_command_context
from .habits import HabitService, build_default_habit_service
from .reports import TraderReportService, build_default_trader_report_service
from .tool_turn import SERVANT_TURN_SCHEMA, TurnOutcome, run_tool_turn

logger = logging.getLogger("agora.servant.turn_worker")

OPENCLAW_DEGRADED_CODE = "OPENCLAW_UPSTREAM_DEGRADED"
_WORKER_ROLES = ("persona",)
_SYSTEM_ROLES = ("system",)
# The lease must outlast the longest provider call; a crashed worker's turn is re-claimed after it.
DEFAULT_LEASE_SECONDS = 600
DEFAULT_PURGE_INTERVAL_SECONDS = 3600
DEFAULT_WEEKLY_TICK_INTERVAL_SECONDS = 3600
WEEKLY_TICK_MAX_TRADER_SCOPES = 50


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_clock_datetime(ts: Optional[str]) -> datetime:
    if ts:
        try:
            parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


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
        weekly_tick_interval_seconds: float = DEFAULT_WEEKLY_TICK_INTERVAL_SECONDS,
        default_tenant_id: Optional[str] = None,
        attribution_port: Optional[Any] = None,
        attribution_port_factory: Optional[Callable[[str], Any]] = None,
        triggers: Optional[Any] = None,
        clock: Callable[[], str] = _utc_now,
        tools: Optional[ServantDataTools] = None,
        habits: Optional[HabitService] = None,
        reports: Optional[TraderReportService] = None,
    ) -> None:
        self.owner = owner
        # SD §4.3: the fixed read-only tool set, resolved here and never by the model.
        self.tools = tools if tools is not None else ServantDataTools(agora_owner=owner)
        # SD §6: every turn carries the trader's habit profile and the servant's live (private)
        # interpretations, so a correction by the trader changes the next answer.
        self.habits = habits if habits is not None else build_default_habit_service(
            owner, journal_reader=getattr(self.tools, "journal_reader", None)
        )
        # SD §9.3: weekly and on-demand trader reports (also invoked via command bar 整理本週報表).
        self.reports = reports if reports is not None else build_default_trader_report_service(
            owner, habit_service_getter=lambda: self.habits, clock=clock
        )
        self.client_factory = client_factory or OpenClawOpsClient
        self.worker_id = worker_id or os.getenv(
            "PANTHEON_AGORA_SERVANT_WORKER_ID", f"servant-turn-worker-{uuid.uuid4().hex[:12]}"
        )
        self.lease_seconds = lease_seconds
        self.purge_interval_seconds = purge_interval_seconds
        self.weekly_tick_interval_seconds = float(weekly_tick_interval_seconds)
        self.default_tenant_id = (
            str(
                default_tenant_id
                or os.getenv("PANTHEON_TENANT_ID")
                or os.getenv("PANTHEON_BFF_TENANT_ID")
                or "default"
            ).strip()
            or "default"
        )
        self._attribution_port = attribution_port
        self._attribution_port_factory = attribution_port_factory
        self._default_triggers = triggers
        self._triggers_by_tenant: Dict[str, Any] = {}
        self.clock = clock
        self._last_purge = 0.0
        self._last_weekly_tick = 0.0
        self._weekly_completed_reports: set[tuple[str, str, str, str]] = set()
        self.metrics: Dict[str, int] = {
            "completed": 0,
            "degraded": 0,
            "failed": 0,
            "purged_sessions": 0,
            "reports_generated": 0,
            "purged_reports": 0,
            "weekly_ticks": 0,
            "weekly_attribution_requests": 0,
        }

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
        report_payload: Optional[Dict[str, Any]] = None
        report_citations: List[Dict[str, Any]] = []
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
            quick_task = str(cmd_ctx.get("quick_task") or "").strip()
            user_text = str(user_message.get("content") or "").strip()
            is_report_quick_task = (
                quick_task in {"整理本週報表", "weekly_trader_report", "trader_report"}
                or user_text == "整理本週報表"
            )
            ctx = ToolContext(
                tenant_id=str(turn.get("tenant_id") or ""),
                user_id=str(turn.get("user_id") or ""),
                operator_id=str(turn.get("operator_id") or ""),
                servant_persona_id=persona_id,
                command_context=cmd_ctx,
            )
            if is_report_quick_task and self.reports is not None:
                try:
                    gen_report = self.reports.generate_report(
                        tenant_id=ctx.tenant_id,
                        user_id=ctx.user_id,
                        servant_persona_id=persona_id,
                        strategy_id=cmd_ctx.get("strategy_id"),
                        trigger="on_demand",
                        actor_roles=_WORKER_ROLES,
                    )
                    answer = str(gen_report.get("narrative") or "")
                    report_citations = list(gen_report.get("citations") or [])
                    report_payload = {
                        "report_id": gen_report.get("report_id"),
                        "trigger": gen_report.get("trigger"),
                        "strategy_id": gen_report.get("strategy_id"),
                        "strategy_version": gen_report.get("strategy_version"),
                        "window_start": gen_report.get("window_start"),
                        "window_end": gen_report.get("window_end"),
                    }
                    self.metrics["reports_generated"] += 1
                except TraderReportDailyCapError as exc:
                    provider_status = "degraded"
                    error = {"code": "TRADER_REPORT_DAILY_CAP_EXCEEDED", "message": str(exc)}
                except Exception as exc:
                    logger.exception("servant report turn %s failed", turn_id)
                    provider_status = "degraded"
                    error = {"code": "SERVANT_REPORT_INTERNAL", "message": type(exc).__name__}
            else:
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
                    "citations": list(outcome.facts) if outcome is not None else report_citations,
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
                        **({"trader_report": report_payload} if report_payload is not None else {}),
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
        if self.reports is not None and hasattr(self.owner, "claim_trader_report"):
            while processed < limit:
                queued_report = self.owner.claim_trader_report(
                    lease_owner=self.worker_id,
                    actor_roles=_WORKER_ROLES,
                    lease_seconds=self.lease_seconds,
                    now=self.clock(),
                )
                if queued_report is None:
                    break
                self.reports.process_queued_report(queued_report, actor_roles=_WORKER_ROLES)
                self.metrics["reports_generated"] += 1
                processed += 1
        self.maybe_run_weekly_tick()
        self.maybe_purge()
        return processed

    def _attribution_port_for_tenant(self, tenant_id: str) -> Any:
        if self._attribution_port is not None:
            return self._attribution_port
        if self._attribution_port_factory is not None:
            return self._attribution_port_factory(tenant_id)
        from services.persona.meeting_triggers import attribution_port_from_env

        return attribution_port_from_env(os.environ, tenant_id)

    def _triggers_for_tenant(self, tenant_id: str) -> Any:
        if self._default_triggers is not None:
            return self._default_triggers
        clean_tenant = str(tenant_id or self.default_tenant_id or "default").strip() or "default"
        mt = self._triggers_by_tenant.get(clean_tenant)
        from services.persona.meeting_triggers import MeetingTriggers

        port = self._attribution_port_for_tenant(clean_tenant)
        if mt is None:
            mt = MeetingTriggers(
                attribution=port,
                now=lambda: _parse_clock_datetime(self.clock()),
            )
            self._triggers_by_tenant[clean_tenant] = mt
        elif mt.attribution is None and port is not None:
            mt.attribution = port
        return mt

    def _discover_active_traders_by_tenant(self) -> Dict[str, List[Dict[str, Any]]]:
        by_tenant: Dict[str, List[Dict[str, Any]]] = {}
        seen: set[tuple[str, str, str]] = set()
        if hasattr(self.owner, "list_active_trader_scopes"):
            try:
                for row in self.owner.list_active_trader_scopes(limit=WEEKLY_TICK_MAX_TRADER_SCOPES):
                    t_id = str(row.get("tenant_id") or "").strip()
                    u_id = str(row.get("user_id") or "").strip()
                    s_id = str(row.get("strategy_id") or "").strip() or None
                    if not t_id or not u_id:
                        continue
                    key = (t_id, u_id, s_id or "__all__")
                    if key in seen:
                        continue
                    seen.add(key)
                    by_tenant.setdefault(t_id, []).append(dict(row))
            except Exception:
                logger.exception("failed to list active trader scopes from Agora owner")
        try:
            from ..trading_room.service import get_active_trading_room_service

            tr_svc = get_active_trading_room_service()
            if tr_svc is not None and hasattr(tr_svc.store, "list_workspace_records"):
                for rec in tr_svc.store.list_workspace_records():
                    t_id = str(rec.get("tenant_id") or "").strip()
                    u_id = str(rec.get("user_id") or "").strip()
                    s_id = str(rec.get("strategy_id") or "").strip() or None
                    if not t_id or not u_id:
                        continue
                    key = (t_id, u_id, s_id or "__all__")
                    if key in seen or len(seen) >= WEEKLY_TICK_MAX_TRADER_SCOPES:
                        continue
                    seen.add(key)
                    by_tenant.setdefault(t_id, []).append({
                        "tenant_id": t_id,
                        "user_id": u_id,
                        "strategy_id": s_id,
                        "servant_persona_id": f"servant:{t_id}:{u_id}",
                    })
        except Exception:
            pass
        if self.default_tenant_id:
            by_tenant.setdefault(self.default_tenant_id, [])
        return by_tenant

    def maybe_run_weekly_tick(self, *, force: bool = False) -> Optional[Dict[str, Any]]:
        """Bounded weekly post-attribution tick invoked by ``run_once`` in ``agora-interaction-worker``.

        Checks at most once per ``weekly_tick_interval_seconds`` (default 3600s) and is idempotent
        per ``(tenant_id, iso_week)``. Requests the weekly attribution run via ``HttpAttributionPort``
        even when no persona cycle runs (SD §13 residual 3) and generates each active trader's
        weekly report (SD §9.3).
        """
        now_mono = time.monotonic()
        if (
            not force
            and self._last_weekly_tick
            and self.weekly_tick_interval_seconds > 0
            and (now_mono - self._last_weekly_tick) < self.weekly_tick_interval_seconds
        ):
            return None
        self._last_weekly_tick = now_mono
        self.metrics["weekly_ticks"] = self.metrics.get("weekly_ticks", 0) + 1
        by_tenant = self._discover_active_traders_by_tenant()
        outcomes: Dict[str, Any] = {}
        for tenant_id, traders in sorted(by_tenant.items()):
            try:
                outcomes[tenant_id] = self.run_weekly_post_attribution_reports(
                    tenant_id=tenant_id,
                    traders=traders,
                )
            except Exception:
                logger.exception("weekly post-attribution report tick failed for tenant %s", tenant_id)
        return outcomes

    def run_weekly_post_attribution_reports(
        self,
        *,
        tenant_id: str,
        traders: Optional[List[Mapping[str, Any]]] = None,
        snapshots: Optional[Any] = None,
        triggers: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run the weekly attribution request (even when no persona cycle runs; SD §13 residual 3)
        via ``MeetingTriggers.run_without_persona_cycle`` and produce each trader's weekly report
        after attribution (SD §9.3).
        """
        clean_tenant = str(tenant_id or self.default_tenant_id or "default").strip() or "default"
        mt = triggers if triggers is not None else self._triggers_for_tenant(clean_tenant)
        snap_map = dict(snapshots) if isinstance(snapshots, Mapping) else {}
        cycle_eval = mt.run_without_persona_cycle(
            tenant_id=clean_tenant,
            snapshots=snap_map,
        )
        attribution_run = (
            cycle_eval.get("weekly_outlier_run")
            if isinstance(cycle_eval, Mapping)
            else None
        )
        attr_status = (
            str(attribution_run.get("status") or "").strip()
            if isinstance(attribution_run, Mapping)
            else ""
        )
        if attr_status == "requested":
            self.metrics["weekly_attribution_requests"] = self.metrics.get("weekly_attribution_requests", 0) + 1

        run_id: Optional[str] = None
        if isinstance(attribution_run, Mapping):
            run_id = str(attribution_run.get("run_id") or attribution_run.get("id") or "") or None
        elif attribution_run is not None:
            run_id = str(getattr(attribution_run, "run_id", getattr(attribution_run, "id", "")) or "") or None

        effective_traders: List[Mapping[str, Any]]
        if traders is not None:
            effective_traders = list(traders)
        elif hasattr(self.owner, "list_active_trader_scopes"):
            effective_traders = list(
                self.owner.list_active_trader_scopes(
                    tenant_id=clean_tenant,
                    limit=WEEKLY_TICK_MAX_TRADER_SCOPES,
                )
            )
        else:
            effective_traders = []

        reports_out: List[Dict[str, Any]] = []
        if self.reports is not None and attr_status in {"requested", "already_requested"}:
            from services.persona.meeting_triggers import iso_week

            now_dt = _parse_clock_datetime(self.clock())
            current_week = str(
                (attribution_run.get("iso_week") if isinstance(attribution_run, Mapping) else None)
                or iso_week(now_dt.date())
            )
            for trader in effective_traders:
                user_id = str(trader.get("user_id") or trader.get("trader_id") or "").strip()
                if not user_id:
                    continue
                persona_id = str(
                    trader.get("servant_persona_id") or f"servant:{clean_tenant}:{user_id}"
                ).strip()
                strategy_id = str(trader.get("strategy_id") or "").strip() or None
                strat_key = strategy_id or "__all__"
                dedup_key = (clean_tenant, current_week, user_id, strat_key)
                before_ids = {
                    str(r.get("report_id"))
                    for r in (
                        self.owner.list_trader_reports(
                            tenant_id=clean_tenant,
                            user_id=user_id,
                            strategy_id=strat_key,
                            trigger="weekly_post_attribution",
                            limit=5,
                        )
                        if hasattr(self.owner, "list_trader_reports")
                        else []
                    )
                }
                rep = self.reports.ensure_weekly_report_after_attribution(
                    tenant_id=clean_tenant,
                    user_id=user_id,
                    servant_persona_id=persona_id,
                    strategy_id=strategy_id,
                    attribution_run_id=run_id,
                    actor_roles=_WORKER_ROLES,
                )
                reports_out.append(rep)
                self._weekly_completed_reports.add(dedup_key)
                if str(rep.get("report_id") or "") not in before_ids:
                    self.metrics["reports_generated"] += 1
        return {
            "tenant_id": clean_tenant,
            "meeting_trigger_record": cycle_eval,
            "attribution_run": attribution_run,
            "attribution_run_id": run_id,
            "reports": reports_out,
        }

    def maybe_purge(self) -> Optional[Dict[str, int]]:
        """Cleanup path: servant sessions untouched for 180 days and expired on-demand trader reports."""
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
        if hasattr(self.owner, "purge_expired_trader_reports"):
            try:
                purged_rep = self.owner.purge_expired_trader_reports(
                    now=self.clock(), actor_roles=_SYSTEM_ROLES
                )
                removed["trader_reports"] = int(purged_rep.get("reports", 0))
                self.metrics["purged_reports"] += int(purged_rep.get("reports", 0))
            except Exception:
                logger.exception("trader report purge failed")
        return removed
