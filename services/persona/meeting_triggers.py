"""Deliberative-meeting triggers hosted by the persona daily cycle (MEETING-TRIGGERS-R2-20261116).

No scheduler is added. A meeting is opened through the consultation route
(``open_persona_meeting``); this module only decides *whether the cycle should ask*,
records why it did not, and never executes anything the meeting proposes.

Triggers (SD_PERSONA_MEETINGS_2026-11-15 sections 5, 6, 12):
  MT-1  the persona's weekly evolution day
  MT-2  >=3 pending lesson candidates, a recorded harness patch candidate, or a completed
        20-trading-day counterfactual window
  MT-3  first enrolled persona's cycle of the ISO week; also requests the weekly outlier
        run from the attribution owner (idempotent per ISO week)
  MT-4  each attribution record without a meeting (including records raised by a drawdown
        removal, a persona drawdown breach or a paper-loss incident)
  MT-6  quarter end, before the paper allocation run (``quarterly_review``)
  consult_request proposals for a deliberative type are opened within the budget, otherwise
  the refusal reason is recorded.

Every threshold comes from ``MeetingTriggerConfig`` (design values as defaults, env override).
No number here is computed by a language model; attribution numbers belong to the owner.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

log = logging.getLogger(__name__)

DELIBERATIVE_TYPES = ("MT-1", "MT-2", "MT-3", "MT-4", "MT-5", "MT-6")
_SLUGS = {"pool_evolution_review": "MT-1", "optimisation_review": "MT-2", "optimization_review": "MT-2",
          "strategy_ideation": "MT-3", "performance_attribution_review": "MT-4",
          "regime_interpretation": "MT-5", "quarterly_review": "MT-6"}
_OPEN_PATCH = {"draft", "previewed"}


@dataclass(frozen=True)
class MeetingTriggerConfig:
    enabled: bool = True
    lesson_candidate_threshold: int = 3          # MT-2: pending lesson candidates
    counterfactual_window_days: int = 20         # MT-2: expression-layer window (trading days)
    max_meetings_per_persona_per_week: int = 3   # M9
    max_meetings_per_cycle: int = 3              # one cycle never floods the route
    max_meetings_per_day: int = 8                # M9 daily budget across this process's cycles
    mt4_max_records_per_cycle: int = 5
    attribution_lookback_days: int = 8
    quarter_end_days: int = 3                    # MT-6: last N days of the quarter and the new quarter's first days
    request_weekly_outlier_run: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MeetingTriggerConfig":
        env = os.environ if env is None else env
        values: dict[str, Any] = {}
        for f in fields(cls):
            raw = env.get("PANTHEON_MEETING_" + f.name.upper())
            if raw is None or raw == "":
                continue
            try:
                values[f.name] = (raw.strip().lower() in {"1", "true", "yes"} if f.type in (bool, "bool")
                                  else int(raw))
            except ValueError:
                log.warning("ignoring invalid meeting trigger setting %s", f.name)
        return cls(**values)


def canonical_type(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.upper() in DELIBERATIVE_TYPES:
        return text.upper()
    return _SLUGS.get(text.lower())


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year:04d}-W{week:02d}"


def quarter_of(day: date) -> str:
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def evolution_weekday(persona: Mapping[str, Any]) -> int:
    """Persona-configured weekday (metadata.meeting_evolution_weekday, 0=Mon) else a stable spread by id."""
    configured = (persona.get("metadata") or {}).get("meeting_evolution_weekday")
    if type(configured) is int and 0 <= configured <= 6:
        return configured
    return int(hashlib.sha256(str(persona["persona_id"]).encode()).hexdigest(), 16) % 5  # Mon-Fri


def _key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:40]


def _reason(exc: Exception) -> str:
    payload = getattr(exc, "payload", None)
    if isinstance(payload, Mapping) and payload.get("reason"):
        return str(payload["reason"])[:120]
    return (type(exc).__name__ + ":" + str(getattr(exc, "status_code", "") or ""))[:120]


class AttributionPort:
    """Attribution owner reads and the weekly run request, over a (method, path, body) transport."""

    def __init__(self, transport: Callable[[str, str, "dict | None"], "tuple[int, Any]"]):
        self._transport = transport

    def request_weekly_run(self, window_end: str) -> dict[str, Any]:
        status, body = self._transport("POST", "/api/attribution/runs", {"kind": "weekly", "window_end": window_end})
        if status not in (200, 201, 202) or not isinstance(body, Mapping):
            raise RuntimeError(f"attribution_run_refused:{status}")
        return dict(body)

    def new_records(self, since: str, limit: int = 50) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = None
        for _ in range(5):
            path = f"/api/attribution/records?since={since}&limit={limit}" + (f"&cursor={cursor}" if cursor else "")
            status, body = self._transport("GET", path, None)
            if status != 200 or not isinstance(body, Mapping):
                raise RuntimeError(f"attribution_records_unavailable:{status}")
            items.extend(body.get("items") or [])
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return items


def attribution_port_from_env(env: Mapping[str, str], tenant: str) -> AttributionPort | None:
    base = env.get("PANTHEON_ATTRIBUTION_API_URL", "")
    token = env.get("PANTHEON_ATTRIBUTION_SERVICE_TOKEN", "")
    if not base or not token:
        return None
    from services.persona.persona_cycle_controller import http_transport
    return AttributionPort(http_transport(base, token, tenant))


def default_opener(persona: Mapping[str, Any], env: Mapping[str, str] | None = None) -> Callable[..., dict[str, Any]]:
    """open_persona_meeting bound to this persona's tenant and service identity."""
    def open_(**kwargs: Any) -> dict[str, Any]:
        from services.consultation.client import ConsultationServiceClient
        client = ConsultationServiceClient(tenant_id=persona["tenant_id"],
                                           service_actor=f"persona:{persona['persona_id']}")
        return client.open_persona_meeting(tenant_id=persona["tenant_id"], **kwargs)
    return open_


class MeetingTriggers:
    def __init__(self, *, config: MeetingTriggerConfig | None = None,
                 opener: Callable[..., dict[str, Any]] | None = None,
                 attribution: AttributionPort | None = None,
                 now: Callable[[], datetime] | None = None):
        self.config = config or MeetingTriggerConfig()
        self._opener = opener
        self.attribution = attribution
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._day_counts: dict[str, int] = {}

    # ------------------------------------------------------------- helpers
    def _opened_this_week(self, snapshots: Mapping[str, Mapping[str, Any]], persona: Mapping[str, Any], week: str) -> int:
        n = 0
        for p in snapshots.values():
            if p.get("tenant_id") != persona["tenant_id"] or persona["persona_id"] not in p.get("persona_ids", []):
                continue
            meetings = (p.get("persona_cycle") or {}).get("meetings") or {}
            n += sum(1 for c in meetings.get("convened", []) if c.get("iso_week") == week)
        return n

    @staticmethod
    def _already(snapshots: Mapping[str, Mapping[str, Any]], persona: Mapping[str, Any], key: str) -> bool:
        for p in snapshots.values():
            if p.get("tenant_id") != persona["tenant_id"]:
                continue
            meetings = (p.get("persona_cycle") or {}).get("meetings") or {}
            if any(c.get("idempotency_key") == key for c in meetings.get("convened", [])):
                return True
        return False

    def _convene(self, ctx: dict[str, Any], meeting_type: str, trigger: str, topic: str, key: str, **body: Any) -> dict | None:
        """Open one meeting inside the budgets; a refusal is recorded with its reason, never raised."""
        persona, record, cfg = ctx["persona"], ctx["record"], self.config
        label = {"meeting_type": meeting_type, "trigger": trigger, "idempotency_key": key}
        if self._already(ctx["snapshots"], persona, key):
            record["not_fired"].append({**label, "reason": "already_convened"})
            return None
        today = ctx["today"].isoformat()
        for name, used, cap in (
                ("cycle_meeting_budget_spent", len(record["convened"]), cfg.max_meetings_per_cycle),
                ("weekly_meeting_budget_spent", ctx["week_used"] + len(record["convened"]),
                 cfg.max_meetings_per_persona_per_week),
                ("daily_meeting_budget_spent", self._day_counts.get(today, 0), cfg.max_meetings_per_day)):
            if used >= cap:
                record["refused"].append({**label, "reason": name})
                return None
        try:
            opener = self._opener or default_opener(persona)
            result = opener(meeting_type=meeting_type, topic=topic, convened_by={"kind": "persona", "id": persona["persona_id"]},
                            idempotency_key=key, iso_week=ctx["week"], **body)
        except Exception as exc:
            record["refused"].append({**label, "reason": _reason(exc)})
            return None
        self._day_counts[today] = self._day_counts.get(today, 0) + 1
        entry = {**label, "meeting_id": result.get("meeting_id"), "status": result.get("status"), "iso_week": ctx["week"]}
        record["convened"].append(entry)
        return entry

    # --------------------------------------------------------------- entry
    def run(self, persona: Mapping[str, Any], observe: Mapping[str, Any], packet: dict[str, Any],
            snapshots: Mapping[str, Mapping[str, Any]], enrolled: Sequence[str]) -> dict[str, Any]:
        """Never raises. Returns the record stored at ``persona_cycle.meetings``."""
        record: dict[str, Any] = {"convened": [], "refused": [], "not_fired": [], "requests": [],
                                  "weekly_outlier_run": None}
        try:
            if not self.config.enabled:
                record["not_fired"].append({"reason": "meeting_triggers_disabled"})
                return record
            today = self.now().astimezone(timezone.utc).date()
            week = iso_week(today)
            ctx = {"persona": persona, "record": record, "snapshots": snapshots, "today": today, "week": week,
                   "week_used": self._opened_this_week(snapshots, persona, week), "packet": packet}
            cycle = packet.get("persona_cycle") or {}
            pid = persona["persona_id"]
            # consult_request proposals first: the persona's own ask outranks the schedule inside the budget
            self._requests(ctx, cycle)
            self._mt1(ctx, observe, pid)
            self._mt2(ctx, observe, cycle, persona)
            first = sorted(enrolled)[0] if enrolled else None
            self._mt3(ctx, pid, first)
            self._mt4(ctx, pid, first, enrolled)
        except Exception as exc:  # the cycle runs without meetings rather than failing
            record["error"] = "meeting_triggers_unavailable:" + type(exc).__name__
        return record

    # ------------------------------------------------------------- triggers
    def _mt1(self, ctx, observe, pid):
        day = ctx["today"]
        if day.weekday() != evolution_weekday(ctx["persona"]):
            ctx["record"]["not_fired"].append({"meeting_type": "MT-1", "reason": "not_weekly_evolution_day"})
            return
        refs = list((observe.get("cycle_subject") or {}).get("member_refs") or [])
        self._convene(ctx, "MT-1", "weekly_evolution_day", f"Pool evolution review for {pid} ({ctx['week']})",
                      _key("MT-1", ctx["persona"]["tenant_id"], pid, ctx["week"]), subject_ref=f"persona:{pid}",
                      evidence_refs=refs[:20])

    def _mt2(self, ctx, observe, cycle, persona):
        cfg, pid = self.config, persona["persona_id"]
        pending = observe.get("pending_lesson_candidates")
        reasons = []
        if type(pending) is int and pending >= cfg.lesson_candidate_threshold:
            reasons.append(f"lessons:{pending}")
        patches = ((persona.get("metadata") or {}).get("persona_harness") or {}).get("patches") or {}
        open_patches = sorted(k for k, v in patches.items() if (v or {}).get("status") in _OPEN_PATCH)
        if open_patches:
            reasons.append("harness_patch:" + ",".join(open_patches[:3]))
        window = observe.get("counterfactual_window")
        window_id = None
        if isinstance(window, Mapping) and type(window.get("trading_days")) is int \
                and window["trading_days"] >= cfg.counterfactual_window_days and window.get("window_id"):
            window_id = str(window["window_id"])
            reasons.append("counterfactual_window:" + window_id)
        if not reasons:
            ctx["record"]["not_fired"].append({"meeting_type": "MT-2", "reason": "no_optimisation_condition",
                                               "pending_lesson_candidates": pending})
            return
        # one meeting per distinct condition set, never one per day while a patch stays open
        self._convene(ctx, "MT-2", ";".join(reasons), f"Optimisation review for {pid}: {'; '.join(reasons)}",
                      _key("MT-2", persona["tenant_id"], pid, sorted(reasons), ctx["week"] if not window_id else window_id),
                      subject_ref=f"persona:{pid}", evidence_refs=[f"harness-patch:{p}" for p in open_patches[:5]])

    def _mt3(self, ctx, pid, first):
        week = ctx["week"]
        if pid != first:
            ctx["record"]["not_fired"].append({"meeting_type": "MT-3", "reason": "not_first_enrolled_persona"})
            return
        tenant = ctx["persona"]["tenant_id"]
        self._convene(ctx, "MT-3", "weekly_first_enrolled_persona", f"Strategy ideation ({week})",
                      _key("MT-3", tenant, week), subject_ref=f"iso-week:{week}")
        if not self.config.request_weekly_outlier_run:
            return
        if self._weekly_run_done(ctx):
            ctx["record"]["weekly_outlier_run"] = {"status": "already_requested", "iso_week": week}
            return
        if self.attribution is None:
            ctx["record"]["weekly_outlier_run"] = {"status": "not_requested", "reason": "attribution_owner_unconfigured"}
            return
        window_end = (ctx["today"] - timedelta(days=ctx["today"].weekday() + 1)).isoformat()  # last Sunday
        try:
            result = self.attribution.request_weekly_run(window_end)
            ctx["record"]["weekly_outlier_run"] = {"status": "requested", "iso_week": week, "window_end": window_end,
                                                   "run_id": result.get("run_id"), "run_status": result.get("status")}
        except Exception as exc:
            ctx["record"]["weekly_outlier_run"] = {"status": "not_requested", "iso_week": week,
                                                   "reason": str(exc)[:120] or type(exc).__name__}

    @staticmethod
    def _weekly_run_done(ctx) -> bool:
        persona, week = ctx["persona"], ctx["week"]
        for p in ctx["snapshots"].values():
            if p.get("tenant_id") != persona["tenant_id"]:
                continue
            run = ((p.get("persona_cycle") or {}).get("meetings") or {}).get("weekly_outlier_run") or {}
            if run.get("status") == "requested" and run.get("iso_week") == week:
                return True
        return False

    def _mt4(self, ctx, pid, first, enrolled):
        record = ctx["record"]
        if self.attribution is None:
            record["not_fired"].append({"meeting_type": "MT-4", "reason": "attribution_owner_unconfigured"})
            return
        since = (ctx["today"] - timedelta(days=self.config.attribution_lookback_days)).isoformat()
        try:
            records = self.attribution.new_records(since)
        except Exception as exc:
            record["not_fired"].append({"meeting_type": "MT-4", "reason": str(exc)[:120] or type(exc).__name__})
            return
        opened = 0
        for rec in sorted(records, key=lambda r: str(r.get("record_id"))):
            rid = rec.get("record_id")
            if not rid or rec.get("meeting_id"):
                continue
            subject_is_persona = rec.get("subject_kind") in {"persona", "pool_member"}
            owner = rec.get("subject_id") if subject_is_persona and rec.get("subject_id") in set(enrolled) else first
            if owner != pid:
                continue  # the persona closest to the finding (or the first enrolled for a trader) convenes
            if opened >= self.config.mt4_max_records_per_cycle:
                record["not_fired"].append({"meeting_type": "MT-4", "record_id": rid, "reason": "per_cycle_record_cap"})
                continue
            opened += 1
            self._convene(ctx, "MT-4", "attribution_record:" + str(rec.get("direction") or ""),
                          f"Performance attribution review {rid}: {rec.get('subject_kind')} {rec.get('subject_id')}",
                          _key("MT-4", ctx["persona"]["tenant_id"], rid), subject_ref=f"{rec.get('subject_kind')}:{rec.get('subject_id')}",
                          attribution_record_id=rid, evidence_refs=[f"attribution-record:{rid}"])
        if not opened:
            record["not_fired"].append({"meeting_type": "MT-4", "reason": "no_new_attribution_record"})

    def _requests(self, ctx, cycle):
        proposals = (cycle.get("output") or {}).get("proposals") or []
        decisions = cycle.get("decisions") or []
        for index, proposal in enumerate(proposals):
            if proposal.get("kind") != "consult_request":
                continue
            params = proposal.get("parameters") or {}
            mtype = canonical_type(params.get("meeting_type"))
            if mtype is None:
                continue  # a gate-panel consult request (e.g. harness patch) stays with its own loop
            pid = ctx["persona"]["persona_id"]
            key = _key("request", ctx["persona"]["tenant_id"], ctx["packet"]["packet_id"], index)
            topic = str(params.get("topic") or proposal.get("reason") or "")[:500] or f"{mtype} requested by {pid}"
            before = len(ctx["record"]["refused"])
            entry = self._convene(ctx, mtype, "persona_consult_request", topic, key,
                                  subject_ref=params.get("subject_ref"),
                                  participants=params.get("participants"))
            outcome = ({"status": "meeting_opened", "meeting_id": entry["meeting_id"]} if entry else
                       {"status": "meeting_refused", "reason": (ctx["record"]["refused"][-1]["reason"]
                        if len(ctx["record"]["refused"]) > before else "already_convened")})
            ctx["record"]["requests"].append({"proposal_index": index, "meeting_type": mtype, **outcome})
            if index < len(decisions):
                decisions[index].update(status=outcome["status"], reason=outcome.get("reason") or "meeting_opened",
                                        **({"meeting_id": outcome["meeting_id"]} if entry else {}))


# ------------------------------------------------------------------- MT-6 ---
def quarter_review_due(today: date, cfg: MeetingTriggerConfig) -> bool:
    """Quarter end: the last N days of a quarter or the first N days of the next (allocation runs after close)."""
    nxt = (today + timedelta(days=cfg.quarter_end_days))
    prev = (today - timedelta(days=cfg.quarter_end_days))
    return quarter_of(nxt) != quarter_of(today) or quarter_of(prev) != quarter_of(today)


def convene_quarterly_review(*, tenant: str, quarter: str, opener: Callable[..., dict[str, Any]],
                             reader: Callable[[str], "dict | None"], chair_persona_id: str,
                             config: MeetingTriggerConfig | None = None) -> dict[str, Any]:
    """Open MT-6 for the closed quarter and read its state; returns what the allocation run needs.

    status: ``ready`` (closed minutes: evidence_ref set), ``pending`` (convened, still running),
    ``refused`` / ``unavailable`` (reason recorded; the allocation keeps its own gates and may proceed).
    """
    try:
        opened = opener(meeting_type="MT-6", topic=f"Quarterly review {quarter}",
                        convened_by={"kind": "persona", "id": chair_persona_id},
                        subject_ref=f"quarter:{quarter}", idempotency_key=_key("MT-6", tenant, quarter))
    except Exception as exc:
        return {"status": "refused", "quarter": quarter, "reason": _reason(exc)}
    meeting_id = opened.get("meeting_id")
    if not meeting_id:
        return {"status": "unavailable", "quarter": quarter, "reason": "meeting_id_missing"}
    try:
        detail = reader(str(meeting_id)) or {}
    except Exception as exc:
        return {"status": "unavailable", "quarter": quarter, "meeting_id": meeting_id, "reason": _reason(exc)}
    state = str(detail.get("status") or opened.get("status") or "")
    if state == "closed" and (detail.get("synthesis") or {}):
        return {"status": "ready", "quarter": quarter, "meeting_id": meeting_id,
                "evidence_ref": f"consultation://meetings/{meeting_id}", "outcome": (detail["synthesis"] or {}).get("outcome")}
    if state in {"refused", "failed"}:
        return {"status": "refused", "quarter": quarter, "meeting_id": meeting_id, "reason": "meeting_" + state}
    return {"status": "pending", "quarter": quarter, "meeting_id": meeting_id}
