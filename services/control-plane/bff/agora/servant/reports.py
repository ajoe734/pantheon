"""Agora Servant Weekly & On-Demand Trader Report (AGORA-SERVANT-REVISIONS-AND-REPORT-20261127, SD §9.3).

Covers one trader and one strategy (or all strategies when none is chosen) over a
window (default 7 days). Every number comes from its canonical owner with ``owner``,
``source`` (``source_id``), and ``as_of``; the language model only writes narrative
around ``{{fN}}`` fact references (verified by ``tool_turn.render_answer``).

Triggers:
  - Weekly after the weekly attribution run (even when no persona cycle runs; SD §13 residual 3).
  - On demand via ``POST /bff/agora/servant/reports`` or command bar ``整理本週報表``
    (capped at 3 on-demand reports per trader per day).

Runtime budget (SD §9.3):
  - Table: ``agora.trader_reports`` (owned by Agora owner ``AgoraTraderReport``).
  - Weekly cap: 26 reports per trader and strategy (~6 months).
  - On-demand cap: 3 per trader per day, retained for 7 days.
  - Row size cap: <= 20 KB (20,480 bytes).
  - No persona, meeting, or order route is ever called (SD §10).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query
from pydantic import BaseModel, Field

from services.agora.store import (
    TRADER_REPORT_MAX_BYTES,
    TRADER_REPORT_ONDEMAND_DAILY_CAP,
    TRADER_REPORT_ONDEMAND_RETENTION_DAYS,
    TRADER_REPORT_WEEKLY_HISTORY_CAP,
    TraderReportDailyCapError,
)
from .tool_turn import _allowed_number_tokens, format_fact_value, render_answer

logger = logging.getLogger("agora.servant.reports")

SERVANT_REPORTS_CAPABILITY = "agora.servant.reports.v1"
ALL_STRATEGIES_KEY = "__all__"

REQUIRED_REPORT_SECTIONS: Tuple[str, ...] = (
    "strategy_version",
    "decisions",
    "paper_book",
    "interventions",
    "shadow_comparison",
    "imitation",
    "habit_changes",
    "stale_data_sources",
    "suggested_next_steps",
)

RUNTIME_BUDGET_SPEC: Dict[str, Any] = {
    "writer_id": "agora.trader_reports",
    "table": "agora.trader_reports",
    "owner": "agora",
    "cadence": (
        "1 weekly report per active trader and strategy after weekly attribution "
        "+ at most 3 on-demand reports per trader per day"
    ),
    "max_row_bytes": TRADER_REPORT_MAX_BYTES,
    "hosted_scale_weekly_rows": 30,
    "hosted_scale_ondemand_daily_rows": 30,
    "growth_bytes_per_day": 686000,
    "weekly_cap_per_trader_strategy": TRADER_REPORT_WEEKLY_HISTORY_CAP,
    "ondemand_daily_cap_per_trader": TRADER_REPORT_ONDEMAND_DAILY_CAP,
    "ondemand_retention_days": TRADER_REPORT_ONDEMAND_RETENTION_DAYS,
    "max_total_rows_hosted": 780,
    "max_total_bytes_hosted": 16384000,
    "cleanup": (
        "AgoraWriteService.prune_trader_reports on each write (26 weekly cap per trader+strategy, "
        "7-day retention for on-demand) + AgoraWriteService.purge_expired_trader_reports in ServantTurnWorker.maybe_purge"
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: Optional[str]) -> datetime:
    if ts:
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_trigger(trigger: Optional[str]) -> str:
    raw = str(trigger or "on_demand").strip().lower()
    if raw in {"weekly", "weekly_post_attribution", "post_attribution", "mt3_weekly"}:
        return "weekly_post_attribution"
    return "on_demand"


def _make_fact(
    fact_id: str,
    *,
    section: str,
    metric_key: str,
    label: str,
    value: Any,
    unit: str,
    owner: str,
    source_id: str,
    as_of: str,
    freshness: str = "fresh",
) -> Dict[str, Any]:
    return {
        "id": fact_id,
        "fact_id": fact_id,
        "section": section,
        "metric_key": metric_key,
        "label": label,
        "value": value,
        "unit": unit,
        "owner": owner,
        "source": source_id,
        "source_id": source_id,
        "as_of": as_of,
        "freshness": freshness,
    }


class TraderReportCreateRequest(BaseModel):
    """Request payload for ``POST /bff/agora/servant/reports``."""

    model_config = {"extra": "forbid"}

    strategy_id: Optional[str] = Field(default=None, max_length=120)
    trigger: str = Field(default="on_demand", max_length=64)
    window_days: int = Field(default=7, ge=1, le=90)
    window_start: Optional[str] = Field(default=None, max_length=40)
    window_end: Optional[str] = Field(default=None, max_length=40)
    attribution_run_id: Optional[str] = Field(default=None, max_length=120)
    defer_to_worker: bool = False
    narrative_override: Optional[str] = Field(default=None, max_length=3000)
    owner_inputs: Optional[Dict[str, Any]] = None


class TraderReportService:
    """Builds, persists, and queries SD §9.3 trader reports via the Agora owner."""

    def __init__(
        self,
        *,
        owner: Any,
        trading_room_service_getter: Optional[Callable[[], Any]] = None,
        habit_service_getter: Optional[Callable[[], Any]] = None,
        narrative_invoker: Optional[Callable[[str, Sequence[Mapping[str, Any]]], Optional[str]]] = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.owner = owner
        self._trading_room_service_getter = trading_room_service_getter
        self._habit_service_getter = habit_service_getter
        self._narrative_invoker = narrative_invoker
        self.clock = clock
        self._idempotency_cache: Dict[str, str] = {}

    def _trading_room_service(self) -> Optional[Any]:
        if self._trading_room_service_getter is not None:
            try:
                return self._trading_room_service_getter()
            except Exception:
                return None
        try:
            from ..trading_room.service import get_active_trading_room_service

            return get_active_trading_room_service()
        except Exception:
            return None

    def _habit_service(self) -> Optional[Any]:
        if self._habit_service_getter is not None:
            try:
                return self._habit_service_getter()
            except Exception:
                return None
        try:
            from .habits import build_default_habit_service

            return build_default_habit_service(self.owner)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Owner data collection (every number carries owner, source_id, as_of)
    # ------------------------------------------------------------------

    def _collect_sections_and_facts(
        self,
        *,
        tenant_id: str,
        user_id: str,
        servant_persona_id: str,
        strategy_id: Optional[str],
        window_start: str,
        window_end: str,
        now: str,
        attribution_run_id: Optional[str] = None,
        owner_inputs: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        overrides = dict(owner_inputs or {})
        clean_strategy = str(strategy_id or "").strip() or ALL_STRATEGIES_KEY
        identity = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "sub": user_id,
            "roles": ["trader"],
        }
        tr_service = self._trading_room_service()
        facts: List[Dict[str, Any]] = []

        def add_fact(
            section: str,
            metric_key: str,
            label: str,
            value: Any,
            unit: str,
            *,
            owner_name: str,
            source_id: str,
            as_of: str,
            freshness: str = "fresh",
        ) -> Dict[str, Any]:
            fid = f"f{len(facts) + 1}"
            fact = _make_fact(
                fid,
                section=section,
                metric_key=metric_key,
                label=label,
                value=value,
                unit=unit,
                owner=owner_name,
                source_id=source_id,
                as_of=as_of,
                freshness=freshness,
            )
            facts.append(fact)
            return fact

        # 1. Strategy version in effect during the window (owner: agora)
        ws: Optional[Dict[str, Any]] = None
        if tr_service is not None and clean_strategy != ALL_STRATEGIES_KEY:
            try:
                ws = tr_service.store.get_workspace_for_strategy(
                    clean_strategy, tenant_id=tenant_id, user_id=user_id
                )
            except Exception:
                ws = None
        elif tr_service is not None:
            try:
                for rec in tr_service.store.list_workspace_records():
                    if rec.get("tenant_id") == tenant_id and rec.get("user_id") == user_id:
                        ws = rec.get("workspace")
                        break
            except Exception:
                ws = None

        sv_in = overrides.get("strategy_version") if isinstance(overrides.get("strategy_version"), Mapping) else {}
        active_version = str(
            sv_in.get("version")
            or (ws or {}).get("strategyVersion")
            or "v1"
        )
        dashboard_version = int(
            sv_in.get("dashboard_version")
            if sv_in.get("dashboard_version") is not None
            else ((ws or {}).get("dashboardVersion") or 1)
        )
        sv_as_of = str(sv_in.get("as_of") or (ws or {}).get("updatedAt") or now)
        sv_source = str(
            sv_in.get("source_id")
            or sv_in.get("source")
            or f"agora:strategy_version:{clean_strategy}:{active_version}"
        )
        f_dash_ver = add_fact(
            "strategy_version",
            "dashboard_version",
            "策略作戰室版面版本",
            dashboard_version,
            "version",
            owner_name="agora",
            source_id=sv_source,
            as_of=sv_as_of,
        )
        strategy_version_section: Dict[str, Any] = {
            "section_id": "strategy_version",
            "owner": "agora",
            "source": sv_source,
            "source_id": sv_source,
            "as_of": sv_as_of,
            "strategy_id": clean_strategy,
            "active_version": active_version,
            "workspace_id": (ws or {}).get("id"),
            "metrics": [f_dash_ver],
            "by_key": {"dashboard_version": f_dash_ver},
        }

        # 2. Decisions offered and decisions taken (owner: agora)
        dec_in = overrides.get("decisions") if isinstance(overrides.get("decisions"), Mapping) else {}
        events: List[Dict[str, Any]] = []
        decision_records: List[Dict[str, Any]] = []
        if tr_service is not None and not dec_in:
            try:
                all_ev = tr_service.list_decision_events(identity=identity, page_size=500).get("items") or []
                for ev in all_ev:
                    if clean_strategy != ALL_STRATEGIES_KEY and ev.get("strategy_id") != clean_strategy:
                        continue
                    events.append(ev)
                for rec in tr_service.store.list_trader_decisions(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    strategy_id=None if clean_strategy == ALL_STRATEGIES_KEY else clean_strategy,
                ):
                    decision_records.append(rec)
            except Exception:
                pass

        counts = {"accept": 0, "modify": 0, "reject": 0, "shadow": 0, "expired": 0}
        if dec_in:
            for k in counts:
                counts[k] = int(dec_in.get(k) or 0)
            offered_count = int(dec_in.get("offered") if dec_in.get("offered") is not None else sum(counts.values()))
        else:
            for rec in decision_records:
                d = str(rec.get("decision") or "").lower()
                if d in {"approve", "accept"}:
                    counts["accept"] += 1
                elif d == "modify":
                    counts["modify"] += 1
                elif d == "reject":
                    counts["reject"] += 1
                elif d in {"shadow", "defer"}:
                    counts["shadow"] += 1
            for ev in events:
                if str(ev.get("state") or "").lower() == "expired":
                    counts["expired"] += 1
            offered_count = max(len(events), sum(counts.values()))

        taken_count = counts["accept"] + counts["modify"] + counts["reject"] + counts["shadow"]
        dec_as_of = str(dec_in.get("as_of") or now)
        dec_source = str(
            dec_in.get("source_id")
            or dec_in.get("source")
            or f"agora:decision_records:{tenant_id}:{user_id}:{clean_strategy}"
        )
        f_offered = add_fact("decisions", "offered", "本週提供決策事件數", offered_count, "decisions", owner_name="agora", source_id=dec_source, as_of=dec_as_of)
        f_taken = add_fact("decisions", "taken", "本週已處置決策數", taken_count, "decisions", owner_name="agora", source_id=dec_source, as_of=dec_as_of)
        f_accept = add_fact("decisions", "accept", "接受決策數", counts["accept"], "decisions", owner_name="agora", source_id=dec_source, as_of=dec_as_of)
        f_modify = add_fact("decisions", "modify", "修改後執行決策數", counts["modify"], "decisions", owner_name="agora", source_id=dec_source, as_of=dec_as_of)
        f_reject = add_fact("decisions", "reject", "拒絕決策數", counts["reject"], "decisions", owner_name="agora", source_id=dec_source, as_of=dec_as_of)
        f_shadow = add_fact("decisions", "shadow", "影子追蹤決策數", counts["shadow"], "decisions", owner_name="agora", source_id=dec_source, as_of=dec_as_of)
        f_expired = add_fact("decisions", "expired", "逾時未決策數", counts["expired"], "decisions", owner_name="agora", source_id=dec_source, as_of=dec_as_of)
        decisions_section: Dict[str, Any] = {
            "section_id": "decisions",
            "owner": "agora",
            "source": dec_source,
            "source_id": dec_source,
            "as_of": dec_as_of,
            "metrics": [f_offered, f_taken, f_accept, f_modify, f_reject, f_shadow, f_expired],
            "by_key": {
                "offered": f_offered,
                "taken": f_taken,
                "accept": f_accept,
                "modify": f_modify,
                "reject": f_reject,
                "shadow": f_shadow,
                "expired": f_expired,
            },
        }

        # 3. Paper book fills, positions, realized/unrealized PnL, active capital/exposure
        #    (owners: paper_execution and telemetry)
        pb_in = overrides.get("paper_book") if isinstance(overrides.get("paper_book"), Mapping) else {}
        pb_readback: Dict[str, Any] = {}
        if tr_service is not None and not pb_in:
            try:
                pb_readback = tr_service.get_trader_paper_book(identity)
            except Exception:
                pb_readback = {}

        pb_meta = pb_readback.get("paper_book") if isinstance(pb_readback.get("paper_book"), Mapping) else {}
        bt_meta = pb_readback.get("binding_telemetry") if isinstance(pb_readback.get("binding_telemetry"), Mapping) else {}
        fills_count = int(
            pb_in.get("fills_count")
            if pb_in.get("fills_count") is not None
            else len(pb_readback.get("fills") or [])
        )
        positions_count = int(
            pb_in.get("positions_count")
            if pb_in.get("positions_count") is not None
            else len(pb_readback.get("positions") or [])
        )
        realized_pnl = float(
            pb_in.get("realized_pnl")
            if pb_in.get("realized_pnl") is not None
            else (pb_meta.get("realized_pnl") or bt_meta.get("realized_pnl") or 0.0)
        )
        unrealized_pnl = float(
            pb_in.get("unrealized_pnl")
            if pb_in.get("unrealized_pnl") is not None
            else (pb_meta.get("unrealized_pnl") or bt_meta.get("unrealized_pnl") or 0.0)
        )
        active_capital = float(
            pb_in.get("active_capital")
            if pb_in.get("active_capital") is not None
            else (pb_meta.get("active_capital") or pb_meta.get("starting_capital") or bt_meta.get("active_capital") or 1000000.0)
        )
        exposure = float(
            pb_in.get("exposure")
            if pb_in.get("exposure") is not None
            else (bt_meta.get("gross_exposure") or pb_meta.get("allocated_capital") or 0.0)
        )
        pb_as_of = str(pb_in.get("as_of") or pb_meta.get("updated_at") or pb_readback.get("snapshot_at") or now)
        pb_source = str(
            pb_in.get("source_id")
            or pb_in.get("source")
            or f"paper_execution:trader_paper_book:{tenant_id}:{user_id}"
        )
        tel_as_of = str(pb_in.get("telemetry_as_of") or bt_meta.get("as_of") or pb_as_of)
        tel_source = str(
            pb_in.get("telemetry_source_id")
            or f"telemetry:binding_telemetry:{tenant_id}:{user_id}"
        )

        f_fills = add_fact("paper_book", "fills_count", "模擬帳本成交筆數", fills_count, "fills", owner_name="paper_execution", source_id=pb_source, as_of=pb_as_of)
        f_pos = add_fact("paper_book", "positions_count", "模擬帳本持倉檔數", positions_count, "symbols", owner_name="paper_execution", source_id=pb_source, as_of=pb_as_of)
        f_rpnl = add_fact("paper_book", "realized_pnl", "模擬帳本已實現損益", realized_pnl, "TWD", owner_name="paper_execution", source_id=pb_source, as_of=pb_as_of)
        f_upnl = add_fact("paper_book", "unrealized_pnl", "模擬帳本未實現損益", unrealized_pnl, "TWD", owner_name="paper_execution", source_id=pb_source, as_of=pb_as_of)
        f_cap = add_fact("paper_book", "active_capital", "模擬帳本有效資金", active_capital, "TWD", owner_name="telemetry", source_id=tel_source, as_of=tel_as_of)
        f_exp = add_fact("paper_book", "exposure", "模擬帳本部位曝險", exposure, "TWD", owner_name="telemetry", source_id=tel_source, as_of=tel_as_of)
        paper_book_section: Dict[str, Any] = {
            "section_id": "paper_book",
            "owner": "paper_execution",
            "telemetry_owner": "telemetry",
            "source": pb_source,
            "source_id": pb_source,
            "telemetry_source_id": tel_source,
            "as_of": pb_as_of,
            "telemetry_as_of": tel_as_of,
            "environment": "paper",
            "metrics": [f_fills, f_pos, f_rpnl, f_upnl, f_cap, f_exp],
            "by_key": {
                "fills_count": f_fills,
                "positions_count": f_pos,
                "realized_pnl": f_rpnl,
                "unrealized_pnl": f_upnl,
                "active_capital": f_cap,
                "exposure": f_exp,
            },
        }

        # 4. Interventions and the week's attribution summary (owner: evaluation)
        int_in = overrides.get("interventions") if isinstance(overrides.get("interventions"), Mapping) else {}
        follow_count = int(
            int_in.get("follow_count")
            if int_in.get("follow_count") is not None
            else counts["accept"]
        )
        override_count = int(
            int_in.get("override_count")
            if int_in.get("override_count") is not None
            else (counts["modify"] + counts["reject"] + counts["shadow"])
        )
        attr_sample_size = int(
            int_in.get("sample_size")
            if int_in.get("sample_size") is not None
            else (follow_count + override_count)
        )
        value_added_score = float(
            int_in.get("value_added_score")
            if int_in.get("value_added_score") is not None
            else 0.0
        )
        int_as_of = str(int_in.get("as_of") or now)
        int_source = str(
            int_in.get("source_id")
            or int_in.get("source")
            or f"evaluation:weekly_attribution:{attribution_run_id or f'{tenant_id}:{user_id}:{window_end[:10]}'}"
        )
        f_follow = add_fact("interventions", "follow_count", "遵循策略建議次數", follow_count, "decisions", owner_name="evaluation", source_id=int_source, as_of=int_as_of)
        f_override = add_fact("interventions", "override_count", "人工干預或覆寫次數", override_count, "decisions", owner_name="evaluation", source_id=int_source, as_of=int_as_of)
        f_vas = add_fact("interventions", "value_added_score", "人工干預附加價值分數", value_added_score, "score", owner_name="evaluation", source_id=int_source, as_of=int_as_of)
        f_asample = add_fact("interventions", "sample_size", "歸因樣本數", attr_sample_size, "decisions", owner_name="evaluation", source_id=int_source, as_of=int_as_of)
        interventions_section: Dict[str, Any] = {
            "section_id": "interventions",
            "owner": "evaluation",
            "source": int_source,
            "source_id": int_source,
            "as_of": int_as_of,
            "attribution_run_id": attribution_run_id or f"attr-weekly-{window_end[:10]}",
            "follow_vs_override": {
                "owner": "evaluation",
                "source": int_source,
                "source_id": int_source,
                "as_of": int_as_of,
                "follow": f_follow,
                "override": f_override,
            },
            "metrics": [f_follow, f_override, f_vas, f_asample],
            "by_key": {
                "follow_count": f_follow,
                "override_count": f_override,
                "value_added_score": f_vas,
                "sample_size": f_asample,
            },
        }

        # 5. Shadow vs executed comparison from IMITATION-TRADER-SHADOW-COMPARISON-20261127 (owner: evaluation)
        sh_in = overrides.get("shadow_comparison") if isinstance(overrides.get("shadow_comparison"), Mapping) else {}
        imitation_comp: Dict[str, Any] = {}
        if tr_service is not None and not sh_in:
            try:
                res = tr_service.get_trader_imitation_comparison(
                    identity,
                    strategy_id=None if clean_strategy == ALL_STRATEGIES_KEY else clean_strategy,
                )
                if isinstance(res, Mapping) and isinstance(res.get("data"), Mapping):
                    imitation_comp = dict(res["data"])
            except Exception:
                imitation_comp = {}

        compared_count = int(
            sh_in.get("compared_decisions")
            if sh_in.get("compared_decisions") is not None
            else (imitation_comp.get("decision_count") or counts["shadow"] + counts["accept"] + counts["modify"])
        )
        mean_delta_bps = float(
            sh_in.get("mean_return_delta_bps")
            if sh_in.get("mean_return_delta_bps") is not None
            else ((imitation_comp.get("summary") or {}).get("mean_return_delta_bps") or 0.0)
        )
        sh_state = str(
            sh_in.get("comparison_state")
            or imitation_comp.get("comparison_state")
            or ("not_enough_decisions" if compared_count < 30 else "compared")
        )
        sh_as_of = str(sh_in.get("as_of") or imitation_comp.get("snapshot_at") or now)
        sh_source = str(
            sh_in.get("source_id")
            or sh_in.get("source")
            or f"evaluation:shadow_comparison:{tenant_id}:{user_id}:{clean_strategy}"
        )
        f_sh_count = add_fact("shadow_comparison", "compared_decisions", "影子與實際對照決策數", compared_count, "decisions", owner_name="evaluation", source_id=sh_source, as_of=sh_as_of)
        f_sh_delta = add_fact("shadow_comparison", "mean_return_delta_bps", "影子對照平均報酬差異(bps)", mean_delta_bps, "bps", owner_name="evaluation", source_id=sh_source, as_of=sh_as_of)
        shadow_comparison_section: Dict[str, Any] = {
            "section_id": "shadow_comparison",
            "owner": "evaluation",
            "source": sh_source,
            "source_id": sh_source,
            "as_of": sh_as_of,
            "comparison_state": sh_state,
            "metrics": [f_sh_count, f_sh_delta],
            "by_key": {
                "compared_decisions": f_sh_count,
                "mean_return_delta_bps": f_sh_delta,
            },
        }

        # 6. Imitation status: candidate version, trainer_feedback (agree, disagree, skip) & promotion blockers
        #    (owner: policy_learning)
        im_in = overrides.get("imitation") if isinstance(overrides.get("imitation"), Mapping) else {}
        feedback_counts = {"agree": 0, "disagree": 0, "skip": 0}
        if im_in and isinstance(im_in.get("trainer_feedback"), Mapping):
            tf = im_in["trainer_feedback"]
            for k in feedback_counts:
                feedback_counts[k] = int(tf.get(k) or 0)
        elif hasattr(self.owner, "list_imitation_feedback"):
            try:
                rows = self.owner.list_imitation_feedback(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    strategy_id=None if clean_strategy == ALL_STRATEGIES_KEY else clean_strategy,
                )
                for row in rows or ():
                    verdict = str((row or {}).get("verdict") or "").lower()
                    if verdict in feedback_counts:
                        feedback_counts[verdict] += 1
            except Exception:
                pass

        cand_obj = imitation_comp.get("candidate") if isinstance(imitation_comp.get("candidate"), Mapping) else {}
        candidate_version = str(
            im_in.get("candidate_version")
            or cand_obj.get("version_id")
            or cand_obj.get("candidate_id")
            or "none"
        )
        blockers_list = list(
            im_in.get("promotion_blockers")
            if isinstance(im_in.get("promotion_blockers"), Sequence)
            and not isinstance(im_in.get("promotion_blockers"), (str, bytes))
            else (
                imitation_comp.get("promotion_blockers")
                or (["not_enough_decisions_min_30"] if compared_count < 30 else [])
            )
        )
        im_as_of = str(im_in.get("as_of") or sh_as_of or now)
        im_source = str(
            im_in.get("source_id")
            or im_in.get("source")
            or f"policy_learning:imitation_candidate:{tenant_id}:{user_id}:{candidate_version}"
        )
        f_agree = add_fact("imitation", "feedback_agree", "模仿學習教練回饋同意數", feedback_counts["agree"], "decisions", owner_name="policy_learning", source_id=im_source, as_of=im_as_of)
        f_disagree = add_fact("imitation", "feedback_disagree", "模仿學習教練回饋不同意數", feedback_counts["disagree"], "decisions", owner_name="policy_learning", source_id=im_source, as_of=im_as_of)
        f_skip = add_fact("imitation", "feedback_skip", "模仿學習教練回饋略過數", feedback_counts["skip"], "decisions", owner_name="policy_learning", source_id=im_source, as_of=im_as_of)
        f_blockers = add_fact("imitation", "promotion_blocker_count", "模仿候選晉升阻礙項數", len(blockers_list), "items", owner_name="policy_learning", source_id=im_source, as_of=im_as_of)
        imitation_section: Dict[str, Any] = {
            "section_id": "imitation",
            "owner": "policy_learning",
            "source": im_source,
            "source_id": im_source,
            "as_of": im_as_of,
            "candidate_version": candidate_version,
            "promotion_blockers": blockers_list,
            "trainer_feedback": {
                "owner": "policy_learning",
                "source": im_source,
                "source_id": im_source,
                "as_of": im_as_of,
                "agree": f_agree,
                "disagree": f_disagree,
                "skip": f_skip,
            },
            "metrics": [f_agree, f_disagree, f_skip, f_blockers],
            "by_key": {
                "feedback_agree": f_agree,
                "feedback_disagree": f_disagree,
                "feedback_skip": f_skip,
                "promotion_blocker_count": f_blockers,
            },
        }

        # 7. Habit profile changes during the window (owner: agora)
        hab_in = overrides.get("habit_changes") if isinstance(overrides.get("habit_changes"), Mapping) else {}
        habit_profile: Dict[str, Any] = {}
        shown_features_count = 0
        habit_decision_count = 0
        habit_version = 1
        habit_pkey = f"hprof:{tenant_id}:{user_id}:{clean_strategy}"
        habit_as_of = now
        if not hab_in:
            h_svc = self._habit_service()
            if h_svc is not None:
                try:
                    h_state = h_svc.read(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        persona_id=servant_persona_id,
                        strategy_id=None if clean_strategy == ALL_STRATEGIES_KEY else clean_strategy,
                    )
                    if isinstance(h_state, Mapping) and isinstance(h_state.get("profile"), Mapping):
                        habit_profile = dict(h_state["profile"])
                except Exception:
                    habit_profile = {}
            if not habit_profile and hasattr(self.owner, "get_habit_profile"):
                try:
                    hp = self.owner.get_habit_profile(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        strategy_id=None if clean_strategy == ALL_STRATEGIES_KEY else clean_strategy,
                    )
                    if isinstance(hp, Mapping):
                        habit_profile = dict(hp)
                except Exception:
                    habit_profile = {}
        if habit_profile:
            habit_pkey = str(habit_profile.get("profile_key") or habit_pkey)
            habit_version = int(habit_profile.get("version") or 1)
            habit_decision_count = int(habit_profile.get("decision_count") or 0)
            habit_as_of = str(habit_profile.get("computed_at") or now)
            shown_features_count = sum(
                1
                for feat in (habit_profile.get("features") or [])
                if isinstance(feat, Mapping) and feat.get("shown")
            )
        if hab_in:
            shown_features_count = int(
                hab_in.get("shown_features_count")
                if hab_in.get("shown_features_count") is not None
                else shown_features_count
            )
            habit_decision_count = int(
                hab_in.get("decision_count")
                if hab_in.get("decision_count") is not None
                else habit_decision_count
            )
            habit_version = int(
                hab_in.get("profile_version")
                if hab_in.get("profile_version") is not None
                else habit_version
            )
            habit_as_of = str(hab_in.get("as_of") or habit_as_of)

        hab_source = str(
            hab_in.get("source_id")
            or hab_in.get("source")
            or f"agora:habit_profile:{habit_pkey}:v{habit_version}"
        )
        f_hab_shown = add_fact("habit_changes", "shown_features_count", "已達樣本門檻之交易習慣特徵數", shown_features_count, "features", owner_name="agora", source_id=hab_source, as_of=habit_as_of)
        f_hab_sample = add_fact("habit_changes", "decision_count", "交易習慣累積決策樣本數", habit_decision_count, "decisions", owner_name="agora", source_id=hab_source, as_of=habit_as_of)
        habit_changes_section: Dict[str, Any] = {
            "section_id": "habit_changes",
            "owner": "agora",
            "source": hab_source,
            "source_id": hab_source,
            "as_of": habit_as_of,
            "profile_key": habit_pkey,
            "metrics": [f_hab_shown, f_hab_sample],
            "by_key": {
                "shown_features_count": f_hab_shown,
                "decision_count": f_hab_sample,
            },
        }

        # 8. Data sources that were stale or unavailable during the window (owner: source_ingest)
        st_in = overrides.get("stale_data_sources") if isinstance(overrides.get("stale_data_sources"), Mapping) else {}
        stale_sources: List[Dict[str, Any]] = []
        if st_in and isinstance(st_in.get("sources"), Sequence):
            for item in st_in["sources"]:
                if isinstance(item, Mapping):
                    s_id = str(item.get("data_source") or item.get("source_id") or "unknown")
                    s_as_of = str(item.get("as_of") or now)
                    stale_sources.append({
                        "data_source": s_id,
                        "status": str(item.get("status") or "stale"),
                        "owner": "source_ingest",
                        "source": f"source_ingest:freshness:{s_id}",
                        "source_id": f"source_ingest:freshness:{s_id}",
                        "as_of": s_as_of,
                    })
        elif ws is not None:
            seen_ds = set()
            for v in ws.get("views") or []:
                for w in v.get("widgets") or []:
                    ds = str(w.get("dataSource") or "").strip()
                    avail = str(w.get("dataAvailability") or "full").strip().lower()
                    if ds and avail in {"partial", "missing", "stale", "unavailable"} and ds not in seen_ds:
                        seen_ds.add(ds)
                        stale_sources.append({
                            "data_source": ds,
                            "status": "unavailable" if avail in {"missing", "unavailable"} else "stale",
                            "owner": "source_ingest",
                            "source": f"source_ingest:freshness:{ds}",
                            "source_id": f"source_ingest:freshness:{ds}",
                            "as_of": str(ws.get("updatedAt") or now),
                        })

        st_as_of = str(st_in.get("as_of") or (stale_sources[0]["as_of"] if stale_sources else now))
        st_source = str(
            st_in.get("source_id")
            or st_in.get("source")
            or f"source_ingest:freshness_summary:{tenant_id}:{clean_strategy}"
        )
        f_stale_cnt = add_fact(
            "stale_data_sources",
            "stale_or_unavailable_count",
            "過期或無法使用之資料來源數",
            len(stale_sources),
            "sources",
            owner_name="source_ingest",
            source_id=st_source,
            as_of=st_as_of,
            freshness="stale" if stale_sources else "fresh",
        )
        stale_data_sources_section: Dict[str, Any] = {
            "section_id": "stale_data_sources",
            "owner": "source_ingest",
            "source": st_source,
            "source_id": st_source,
            "as_of": st_as_of,
            "sources": stale_sources,
            "metrics": [f_stale_cnt],
            "by_key": {
                "stale_or_unavailable_count": f_stale_cnt,
            },
        }

        # 9. Suggested next steps (a workshop revision, a workspace proposal, or a clarification question)
        next_in = overrides.get("suggested_next_steps") if isinstance(overrides.get("suggested_next_steps"), Mapping) else {}
        pending_proposals_count = 0
        if tr_service is not None and ws is not None:
            try:
                rev_list = tr_service.store.list_workspace_revision_proposals(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=str(ws.get("id") or ""),
                    strategy_id=clean_strategy if clean_strategy != ALL_STRATEGIES_KEY else None,
                )
                pending_proposals_count = int(rev_list.get("pending_count") or 0)
            except Exception:
                pending_proposals_count = 0
        if next_in.get("pending_proposals_count") is not None:
            pending_proposals_count = int(next_in["pending_proposals_count"])

        suggestions: List[Dict[str, Any]] = []
        if pending_proposals_count > 0:
            suggestions.append({
                "kind": "workspace_proposal",
                "summary": "檢視並決定目前待審核的作戰室版面修訂提案（接受或拒絕）。",
                "owner": "agora",
                "source": sv_source,
                "source_id": sv_source,
                "as_of": now,
            })
        if stale_sources:
            suggestions.append({
                "kind": "workspace_proposal",
                "summary": "針對過期或暫時缺漏的資料來源，評估僕人提出的替代元件修訂提案。",
                "owner": "source_ingest",
                "source": st_source,
                "source_id": st_source,
                "as_of": st_as_of,
            })
        if override_count > follow_count:
            suggestions.append({
                "kind": "workshop_revision",
                "summary": "本週人工覆寫次數高於遵循建議次數，建議於策略工坊檢討進出場條件或風控門檻。",
                "owner": "evaluation",
                "source": int_source,
                "source_id": int_source,
                "as_of": int_as_of,
            })
        if not suggestions:
            suggestions.append({
                "kind": "clarification_question",
                "summary": "維持既有策略版本並持續累積決策樣本；若觀察視窗或偏好改變可隨時向僕人提出釐清。",
                "owner": "agora",
                "source": dec_source,
                "source_id": dec_source,
                "as_of": dec_as_of,
            })

        next_source = str(
            next_in.get("source_id")
            or next_in.get("source")
            or f"agora:servant_next_steps:{tenant_id}:{user_id}:{clean_strategy}"
        )
        f_next_cnt = add_fact(
            "suggested_next_steps",
            "suggestion_count",
            "建議下一步行動項數",
            len(suggestions),
            "items",
            owner_name="agora",
            source_id=next_source,
            as_of=now,
        )
        suggested_next_steps_section: Dict[str, Any] = {
            "section_id": "suggested_next_steps",
            "owner": "agora",
            "source": next_source,
            "source_id": next_source,
            "as_of": now,
            "items": suggestions,
            "metrics": [f_next_cnt],
            "by_key": {
                "suggestion_count": f_next_cnt,
            },
        }

        sections = {
            "strategy_version": strategy_version_section,
            "decisions": decisions_section,
            "paper_book": paper_book_section,
            "interventions": interventions_section,
            "shadow_comparison": shadow_comparison_section,
            "imitation": imitation_section,
            "habit_changes": habit_changes_section,
            "stale_data_sources": stale_data_sources_section,
            "suggested_next_steps": suggested_next_steps_section,
        }
        return sections, facts

    def _default_narrative_template(
        self,
        *,
        sections: Mapping[str, Mapping[str, Any]],
    ) -> str:
        """Deterministic zh-TW template using only ``{{fN}}`` references for numeric values."""
        sv = sections["strategy_version"]
        dec = sections["decisions"]["by_key"]
        pb = sections["paper_book"]["by_key"]
        inter = sections["interventions"]["by_key"]
        sh = sections["shadow_comparison"]["by_key"]
        im = sections["imitation"]["by_key"]
        hab = sections["habit_changes"]["by_key"]
        stale = sections["stale_data_sources"]["by_key"]
        nxt = sections["suggested_next_steps"]["by_key"]

        lines = [
            f"【本週交易員報告】策略版本：{sv.get('active_version')}（作戰室版面第 {{{{{sv['by_key']['dashboard_version']['id']}}}}} 版）。",
            (
                f"一、決策統計：本週共提供 {{{{{dec['offered']['id']}}}}} 筆決策事件，已處置 {{{{{dec['taken']['id']}}}}} 筆"
                f"（接受 {{{{{dec['accept']['id']}}}}} 筆、修改 {{{{{dec['modify']['id']}}}}} 筆、"
                f"拒絕 {{{{{dec['reject']['id']}}}}} 筆、影子追蹤 {{{{{dec['shadow']['id']}}}}} 筆、逾時 {{{{{dec['expired']['id']}}}}} 筆）。"
            ),
            (
                f"二、模擬帳本（Paper Book）：累計成交 {{{{{pb['fills_count']['id']}}}}} 筆，目前持倉 {{{{{pb['positions_count']['id']}}}}} 檔，"
                f"已實現損益 {{{{{pb['realized_pnl']['id']}}}}}，未實現損益 {{{{{pb['unrealized_pnl']['id']}}}}}，"
                f"有效資金 {{{{{pb['active_capital']['id']}}}}}，部位曝險 {{{{{pb['exposure']['id']}}}}}。"
            ),
            (
                f"三、人工干預與週歸因：遵循策略建議 {{{{{inter['follow_count']['id']}}}}} 次、人工覆寫 {{{{{inter['override_count']['id']}}}}} 次，"
                f"歸因樣本數 {{{{{inter['sample_size']['id']}}}}} 筆，附加價值分數 {{{{{inter['value_added_score']['id']}}}}}。"
            ),
            (
                f"四、影子與實際對照：對照決策數 {{{{{sh['compared_decisions']['id']}}}}} 筆，"
                f"平均報酬差異 {{{{{sh['mean_return_delta_bps']['id']}}}}} bps。"
            ),
            (
                f"五、模仿學習狀態：教練回饋同意 {{{{{im['feedback_agree']['id']}}}}} 筆、"
                f"不同意 {{{{{im['feedback_disagree']['id']}}}}} 筆、略過 {{{{{im['feedback_skip']['id']}}}}} 筆，"
                f"目前晉升阻礙共 {{{{{im['promotion_blocker_count']['id']}}}}} 項。"
            ),
            (
                f"六、交易習慣與資料新鮮度：已達門檻之習慣特徵 {{{{{hab['shown_features_count']['id']}}}}} 項"
                f"（累積樣本 {{{{{hab['decision_count']['id']}}}}} 筆）；"
                f"過期或暫時缺漏之資料來源 {{{{{stale['stale_or_unavailable_count']['id']}}}}} 項。"
            ),
            f"七、建議下一步：共 {{{{{nxt['suggestion_count']['id']}}}}} 項行動建議。",
        ]
        return "\n".join(lines)

    def _render_report_narrative(
        self,
        *,
        sections: Mapping[str, Mapping[str, Any]],
        facts: Sequence[Mapping[str, Any]],
        active_version: str,
        narrative_override: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], str]:
        facts_by_id = {str(f["id"]): f for f in facts}
        raw_template: Optional[str] = narrative_override
        narrative_mode = "deterministic_owner_facts"
        if raw_template is None and self._narrative_invoker is not None:
            try:
                candidate = self._narrative_invoker("整理本週交易員報表", facts)
                if isinstance(candidate, str) and candidate.strip():
                    raw_template = candidate.strip()
                    narrative_mode = "model_narrative_guarded"
            except Exception as exc:
                logger.warning("trader report narrative invoker degraded: %s", type(exc).__name__)
                raw_template = None
        if raw_template is None:
            raw_template = self._default_narrative_template(sections=sections)
        else:
            if narrative_override is not None:
                narrative_mode = "override_guarded"

        allowed_tokens = _allowed_number_tokens(facts, active_version, [active_version])
        rendered, used_ids, guard_report = render_answer(
            raw_template,
            facts_by_id,
            allowed_tokens=allowed_tokens,
        )
        cited_facts = [dict(facts_by_id[fid]) for fid in used_ids if fid in facts_by_id]
        if not cited_facts:
            cited_facts = [dict(f) for f in facts]
        return rendered, cited_facts, guard_report, narrative_mode

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def generate_report(
        self,
        *,
        tenant_id: str,
        user_id: str,
        servant_persona_id: str,
        strategy_id: Optional[str] = None,
        trigger: str = "on_demand",
        window_days: int = 7,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        attribution_run_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        defer_to_worker: bool = False,
        narrative_override: Optional[str] = None,
        owner_inputs: Optional[Mapping[str, Any]] = None,
        actor_roles: Sequence[str] = ("trader",),
    ) -> Dict[str, Any]:
        """Create and persist a weekly or on-demand trader report."""
        norm_trigger = _normalize_trigger(trigger)
        clean_strategy = str(strategy_id or "").strip() or ALL_STRATEGIES_KEY
        if idempotency_key:
            cache_key = f"{tenant_id}:{user_id}:{idempotency_key}"
            cached_report_id = self._idempotency_cache.get(cache_key)
            if cached_report_id:
                existing = self.owner.get_trader_report(
                    cached_report_id, tenant_id=tenant_id, user_id=user_id
                )
                if existing is not None:
                    return existing

        now = self.clock()
        end_dt = _parse_iso(window_end or now)
        start_dt = _parse_iso(window_start) if window_start else (end_dt - timedelta(days=int(window_days or 7)))
        win_start = _format_iso(start_dt)
        win_end = _format_iso(end_dt)

        sections, facts = self._collect_sections_and_facts(
            tenant_id=tenant_id,
            user_id=user_id,
            servant_persona_id=servant_persona_id,
            strategy_id=clean_strategy,
            window_start=win_start,
            window_end=win_end,
            now=now,
            attribution_run_id=attribution_run_id,
            owner_inputs=owner_inputs,
        )
        active_version = str(sections["strategy_version"].get("active_version") or "v1")
        narrative, citations, number_guard, narrative_mode = self._render_report_narrative(
            sections=sections,
            facts=facts,
            active_version=active_version,
            narrative_override=narrative_override,
        )

        report_id = f"trpt-{uuid.uuid4().hex[:16]}"
        expires_at: Optional[str] = None
        if norm_trigger == "on_demand":
            expires_at = _format_iso(_parse_iso(now) + timedelta(days=TRADER_REPORT_ONDEMAND_RETENTION_DAYS))
        iso_year, iso_wk, _ = end_dt.isocalendar()
        cadence_val = "weekly" if norm_trigger == "weekly_post_attribution" else "on_demand"
        iso_week_val = str(attribution_run_id or f"{iso_year:04d}-W{iso_wk:02d}")

        report: Dict[str, Any] = {
            "report_id": report_id,
            "spec_version": "agora.trader_report.v1",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "trader_id": user_id,
            "servant_persona_id": servant_persona_id,
            "strategy_id": clean_strategy,
            "strategy_version": active_version,
            "trigger": norm_trigger,
            "cadence": cadence_val,
            "day": now[:10],
            "report_day": now[:10],
            "iso_week": iso_week_val,
            "window_start": win_start,
            "window_end": win_end,
            "window_days": int(window_days or 7),
            "status": "queued" if defer_to_worker else "completed",
            "attribution_run_id": attribution_run_id or sections["interventions"].get("attribution_run_id"),
            "sections": sections,
            "facts": facts,
            "citations": citations,
            "narrative": narrative,
            "narrative_mode": narrative_mode,
            "number_guard": number_guard,
            "no_persona_or_meeting_proof": {
                "persona_calls": 0,
                "meeting_calls": 0,
                "order_routes_called": 0,
            },
            "runtime_budget": RUNTIME_BUDGET_SPEC,
            "created_at": now,
            "updated_at": now,
            **({"expires_at": expires_at} if expires_at else {}),
        }

        _created, stored = self.owner.put_trader_report(
            report,
            actor_roles,
            enforce_daily_cap=(norm_trigger == "on_demand"),
            now=now,
        )
        if idempotency_key:
            self._idempotency_cache[f"{tenant_id}:{user_id}:{idempotency_key}"] = str(stored.get("report_id") or report_id)
        return dict(stored)

    def process_queued_report(
        self,
        report: Mapping[str, Any],
        *,
        actor_roles: Sequence[str] = ("persona", "system"),
    ) -> Dict[str, Any]:
        """Complete a queued trader report claimed by ``agora-interaction-worker``."""
        now = self.clock()
        tenant_id = str(report.get("tenant_id") or "")
        user_id = str(report.get("user_id") or report.get("trader_id") or "")
        servant_persona_id = str(report.get("servant_persona_id") or "")
        strategy_id = str(report.get("strategy_id") or ALL_STRATEGIES_KEY)
        sections, facts = self._collect_sections_and_facts(
            tenant_id=tenant_id,
            user_id=user_id,
            servant_persona_id=servant_persona_id,
            strategy_id=strategy_id,
            window_start=str(report.get("window_start") or ""),
            window_end=str(report.get("window_end") or now),
            now=now,
            attribution_run_id=report.get("attribution_run_id"),
        )
        active_version = str(sections["strategy_version"].get("active_version") or "v1")
        narrative, citations, number_guard, narrative_mode = self._render_report_narrative(
            sections=sections,
            facts=facts,
            active_version=active_version,
        )
        completed = {
            **dict(report),
            "status": "completed",
            "strategy_version": active_version,
            "sections": sections,
            "facts": facts,
            "citations": citations,
            "narrative": narrative,
            "narrative_mode": narrative_mode,
            "number_guard": number_guard,
            "updated_at": now,
            "completed_at": now,
        }
        _created, stored = self.owner.put_trader_report(
            completed,
            actor_roles,
            enforce_daily_cap=False,
            now=now,
        )
        return dict(stored)

    def ensure_weekly_report_after_attribution(
        self,
        *,
        tenant_id: str,
        user_id: str,
        servant_persona_id: str,
        strategy_id: Optional[str] = None,
        attribution_run_id: Optional[str] = None,
        actor_roles: Sequence[str] = ("persona", "system"),
    ) -> Dict[str, Any]:
        """Produce the weekly trader report after the weekly attribution run (SD §9.3, AC2, AC3).

        Idempotent per ``(tenant_id, user_id, strategy_id, attribution_run_id or iso_week)``.
        """
        now = self.clock()
        clean_strategy = str(strategy_id or "").strip() or ALL_STRATEGIES_KEY
        now_dt = _parse_iso(now)
        iso_year, iso_week, _ = now_dt.isocalendar()
        run_id = attribution_run_id or f"attr-weekly-{tenant_id}-{iso_year}W{iso_week:02d}"
        existing = self.owner.list_trader_reports(
            tenant_id=tenant_id,
            user_id=user_id,
            strategy_id=clean_strategy,
            trigger="weekly_post_attribution",
            limit=5,
        )
        for row in existing:
            if str(row.get("attribution_run_id") or "") == run_id:
                return row
        return self.generate_report(
            tenant_id=tenant_id,
            user_id=user_id,
            servant_persona_id=servant_persona_id,
            strategy_id=clean_strategy,
            trigger="weekly_post_attribution",
            window_days=7,
            attribution_run_id=run_id,
            actor_roles=actor_roles,
        )

    def get_report(
        self,
        report_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        return self.owner.get_trader_report(report_id, tenant_id=tenant_id, user_id=user_id)

    def list_reports(
        self,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
        trigger: Optional[str] = None,
        limit: int = TRADER_REPORT_WEEKLY_HISTORY_CAP,
    ) -> List[Dict[str, Any]]:
        norm_trig = _normalize_trigger(trigger) if trigger else None
        return self.owner.list_trader_reports(
            tenant_id=tenant_id,
            user_id=user_id,
            strategy_id=strategy_id,
            trigger=norm_trig,
            limit=limit,
        )


def build_default_trader_report_service(
    owner: Any,
    *,
    trading_room_service_getter: Optional[Callable[[], Any]] = None,
    habit_service_getter: Optional[Callable[[], Any]] = None,
    clock: Callable[[], str] = _utc_now,
) -> Optional[TraderReportService]:
    if owner is None or not hasattr(owner, "put_trader_report"):
        return None
    return TraderReportService(
        owner=owner,
        trading_room_service_getter=trading_room_service_getter,
        habit_service_getter=habit_service_getter,
        clock=clock,
    )


def create_servant_reports_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Callable[[], Any] = lambda: None,
    report_service: Optional[Any] = None,
) -> APIRouter:
    """Owner-scoped trader report routes:
      - ``GET /bff/agora/servant/reports``
      - ``GET /bff/agora/servant/reports/{report_id}``
      - ``POST /bff/agora/servant/reports``
    """
    from services.control_plane.bff.models import ErrorCode

    from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
    from .router import (
        _extract_caller_identity,
        _raise_scope_error,
        _servant_persona_id_for_scope,
    )

    router = APIRouter(tags=["agora-servant-reports"])
    service_cache: Dict[str, Any] = {}

    def _service() -> TraderReportService:
        resolved = report_service() if callable(report_service) else report_service
        if resolved is not None:
            return resolved
        cached = service_cache.get("service")
        if cached is None:
            from .router import _lazy_default_servant_session_owner

            owner = _lazy_default_servant_session_owner()
            if owner is not None:
                cached = build_default_trader_report_service(owner, clock=utc_now)
            if cached is None:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Agora owner is unavailable for trader reports",
                    "trader_report_owner_unavailable",
                    precondition_failed="agora_owner",
                )
            service_cache["service"] = cached
        return cached

    def _caller(
        authorization: Optional[str],
        pantheon_session: Optional[str],
        tenant: Optional[str],
        *,
        write: bool,
    ) -> Tuple[Any, Any, str]:
        identity = _extract_caller_identity(extract_identity, authorization, pantheon_session)
        (require_write_role if write else require_read_role)(identity)
        try:
            scope = resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=tenant,
            )
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)
        return identity, scope, _servant_persona_id_for_scope(get_read_store(), scope)

    def _not_found() -> HTTPException:
        return bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Trader report not found",
            "trader_report_not_found",
            precondition_failed="trader_report_owner_scope",
        )

    def _envelope(data: Dict[str, Any], scope: Any, *, read_only: bool = True) -> Dict[str, Any]:
        return {
            "status": "ok",
            "data": data,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": SERVANT_REPORTS_CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "read_only": read_only,
                "advice_only": True,
                "execution_authority": "none",
                "environment_ceiling": "paper",
                "no_order_route_proof": "agora_trader_report_advice_only",
                "runtime_budget": RUNTIME_BUDGET_SPEC,
            },
        }

    @router.get("/bff/agora/servant/reports")
    def list_servant_trader_reports(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        strategy_id: Optional[str] = Query(default=None, max_length=120),
        trigger: Optional[str] = Query(default=None, max_length=64),
        trader_id: Optional[str] = Query(default=None, max_length=200),
        user_id: Optional[str] = Query(default=None, max_length=200),
        limit: int = Query(default=TRADER_REPORT_WEEKLY_HISTORY_CAP, ge=1, le=50),
    ) -> Dict[str, Any]:
        _identity, scope, persona_id = _caller(
            authorization, pantheon_session, x_tenant_id or x_pantheon_tenant, write=False
        )
        if trader_id not in (None, "", scope.user_id) or user_id not in (None, "", scope.user_id):
            raise _not_found()
        svc = _service()
        items = svc.list_reports(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            strategy_id=strategy_id,
            trigger=trigger,
            limit=limit,
        )
        today = svc.clock()[:10]
        ondemand_used = svc.owner.count_on_demand_trader_reports_for_day(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            day=today,
            now=svc.clock(),
        )
        return _envelope(
            {
                "items": items,
                "total": len(items),
                "servant_persona_id": persona_id,
                "on_demand_daily_used": ondemand_used,
                "on_demand_daily_cap": TRADER_REPORT_ONDEMAND_DAILY_CAP,
                "weekly_history_cap": TRADER_REPORT_WEEKLY_HISTORY_CAP,
                "runtime_budget": RUNTIME_BUDGET_SPEC,
            },
            scope,
            read_only=True,
        )

    @router.get("/bff/agora/servant/reports/{report_id}")
    def get_servant_trader_report(
        report_id: str,
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        trader_id: Optional[str] = Query(default=None, max_length=200),
        user_id: Optional[str] = Query(default=None, max_length=200),
    ) -> Dict[str, Any]:
        _identity, scope, _persona_id = _caller(
            authorization, pantheon_session, x_tenant_id or x_pantheon_tenant, write=False
        )
        if trader_id not in (None, "", scope.user_id) or user_id not in (None, "", scope.user_id):
            raise _not_found()
        report = _service().get_report(
            report_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        if report is None:
            raise _not_found()
        return _envelope(report, scope, read_only=True)

    @router.post("/bff/agora/servant/reports", status_code=201)
    def create_servant_trader_report(
        body: Optional[TraderReportCreateRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        _identity, scope, persona_id = _caller(
            authorization, pantheon_session, x_tenant_id or x_pantheon_tenant, write=True
        )
        req = body or TraderReportCreateRequest()
        try:
            report = _service().generate_report(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                servant_persona_id=persona_id,
                strategy_id=req.strategy_id,
                trigger=req.trigger,
                window_days=req.window_days,
                window_start=req.window_start,
                window_end=req.window_end,
                attribution_run_id=req.attribution_run_id,
                idempotency_key=idempotency_key,
                defer_to_worker=req.defer_to_worker,
                narrative_override=req.narrative_override,
                owner_inputs=req.owner_inputs,
                actor_roles=list(scope.roles) or ["trader"],
            )
        except TraderReportDailyCapError as exc:
            rate_code = getattr(ErrorCode, "RATE_LIMITED", ErrorCode.RESOURCE_CONFLICT)
            raise bff_error(
                429,
                rate_code,
                str(exc),
                "trader_report_daily_cap_exceeded",
                precondition_failed="trader_report_daily_cap",
            ) from exc
        except ValueError as exc:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                str(exc),
                "trader_report_payload_too_large",
                precondition_failed="trader_report_size_cap",
            ) from exc
        return _envelope(report, scope, read_only=False)

    return router


__all__ = [
    "ALL_STRATEGIES_KEY",
    "REQUIRED_REPORT_SECTIONS",
    "RUNTIME_BUDGET_SPEC",
    "SERVANT_REPORTS_CAPABILITY",
    "TraderReportCreateRequest",
    "TraderReportService",
    "build_default_trader_report_service",
    "create_servant_reports_router",
]
