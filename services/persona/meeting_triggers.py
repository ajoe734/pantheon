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
  MT-5  a governed RegimeState transition on the macro axis or on the primary axis of any
        enrolled pool (never from a missing or proposed record)
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


# A meeting whose consultation work dead-lettered past its recovery budget was never held; its
# idempotency key is released for a bounded number of replacements (``<key>:retry-<n>``).
MAX_MEETING_REPLACEMENTS = 2


def default_meeting_reader(persona: Mapping[str, Any]) -> Callable[[str], "dict | None"]:
    """meeting_id -> execution state, from one tenant-scoped read of the dead-lettered work items."""
    cache: dict[str, dict] = {}

    def read(meeting_id: str):
        if not cache:
            from services.consultation.client import ConsultationServiceClient
            from services.consultation.workflow_state import recovery_exhausted
            client = ConsultationServiceClient(tenant_id=persona["tenant_id"], service_actor=f"persona:{persona['persona_id']}")
            listing = client._request_json("GET", "/api/consult/workflows?status=dead_letter", tenant_id=persona["tenant_id"])
            for row in (listing or {}).get("items") or []:
                cache[str(row.get("request_id"))] = {"status": row.get("status"), "recovery_exhausted": recovery_exhausted(row)}
            cache.setdefault("", {})
        return {"meeting_id": meeting_id, "execution_state": cache.get(meeting_id)}
    return read


class MeetingTriggers:
    def __init__(self, *, config: MeetingTriggerConfig | None = None,
                 opener: Callable[..., dict[str, Any]] | None = None,
                 reader: Callable[[str], "dict | None"] | None = None,
                 attribution: AttributionPort | None = None,
                 now: Callable[[], datetime] | None = None):
        self.config = config or MeetingTriggerConfig()
        self._opener = opener
        self._reader = reader
        self.attribution = attribution
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._day_counts: dict[str, int] = {}
        self._convened_keys: set[tuple[str, str]] = set()

    # ------------------------------------------------------------- helpers
    def _opened_this_week(self, snapshots: Mapping[str, Mapping[str, Any]], persona: Mapping[str, Any], week: str) -> int:
        n = 0
        for p in snapshots.values():
            if p.get("tenant_id") != persona["tenant_id"] or persona["persona_id"] not in p.get("persona_ids", []):
                continue
            meetings = (p.get("persona_cycle") or {}).get("meetings") or {}
            n += sum(1 for c in meetings.get("convened", []) if c.get("iso_week") == week)
        return n

    def _convened_entries(self, snapshots: Mapping[str, Mapping[str, Any]], persona: Mapping[str, Any], key: str) -> list[dict]:
        out: list[dict] = []
        for p in snapshots.values():
            if p.get("tenant_id") != persona["tenant_id"]:
                continue
            meetings = (p.get("persona_cycle") or {}).get("meetings") or {}
            out.extend(c for c in meetings.get("convened", []) if c.get("idempotency_key") == key)
        return out

    def _not_held(self, persona: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> list[str]:
        """Meeting ids whose consultation work dead-lettered with its recovery budget spent.

        Anything else (unreadable, running, other dead-letter reasons) still counts as convened.
        """
        reader = self._reader or getattr(self, "_run_reader", None)
        if reader is None:  # one dead-letter listing per run, never stale across cycles
            try:
                reader = self._run_reader = default_meeting_reader(persona)
            except Exception:
                return []
        ids = [str(e["meeting_id"]) for e in entries if e.get("meeting_id")]
        if not ids:
            return []
        failed = []
        for meeting_id in dict.fromkeys(ids):
            try:
                state = (reader(meeting_id) or {}).get("execution_state") or {}
            except Exception:
                return []
            if state.get("status") == "dead_letter" and state.get("recovery_exhausted") is True:
                failed.append(meeting_id)
        return failed if len(failed) == len(set(ids)) else []

    def _already(self, snapshots: Mapping[str, Mapping[str, Any]], persona: Mapping[str, Any], key: str) -> bool:
        if (str(persona.get("tenant_id") or ""), key) in self._convened_keys:
            return True
        return bool(self._convened_entries(snapshots, persona, key))

    def _resolve_key(self, ctx: dict[str, Any], key: str) -> tuple[str | None, list[str]]:
        """The idempotency key to open under, or None when the meeting counts as held.

        Returns ``(key, earlier_not_held_meeting_ids)``.
        """
        persona, snapshots = ctx["persona"], ctx["snapshots"]
        not_held: list[str] = []
        for attempt in range(MAX_MEETING_REPLACEMENTS + 1):
            candidate = key if attempt == 0 else f"{key}:retry-{attempt}"
            if (str(persona.get("tenant_id") or ""), candidate) in self._convened_keys:
                return None, not_held
            entries = self._convened_entries(snapshots, persona, candidate)
            if not entries:
                return candidate, not_held
            failed = self._not_held(persona, entries)
            if not failed:
                return None, not_held
            not_held.extend(failed)
        return None, not_held

    def _convene(self, ctx: dict[str, Any], meeting_type: str, trigger: str, topic: str, key: str, **body: Any) -> dict | None:
        """Open one meeting inside the budgets; a refusal is recorded with its reason, never raised."""
        persona, record, cfg = ctx["persona"], ctx["record"], self.config
        label = {"meeting_type": meeting_type, "trigger": trigger, "idempotency_key": key}
        resolved, not_held = self._resolve_key(ctx, key)
        released = ctx.setdefault("released_meetings", set())
        released.update(not_held)  # a meeting that never ran does not spend the weekly budget
        for meeting_id in not_held:
            record.setdefault("not_held", []).append(
                {**label, "meeting_id": meeting_id, "reason": "execution_failed_recovery_exhausted"})
        if resolved is None:
            record["not_fired"].append({**label, "reason": "already_convened" if not not_held else "replacement_budget_spent"})
            return None
        key = label["idempotency_key"] = resolved
        today = ctx["today"].isoformat()
        for name, used, cap in (
                ("cycle_meeting_budget_spent", len(record["convened"]), cfg.max_meetings_per_cycle),
                ("weekly_meeting_budget_spent", ctx["week_used"] - len(released) + len(record["convened"]),
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
        self._convened_keys.add((str(persona.get("tenant_id") or ""), key))
        entry = {**label, "meeting_id": result.get("meeting_id"), "status": result.get("status"), "iso_week": ctx["week"]}
        record["convened"].append(entry)
        return entry

    # --------------------------------------------------------------- entry
    def run(self, persona: Mapping[str, Any], observe: Mapping[str, Any], packet: dict[str, Any],
            snapshots: Mapping[str, Mapping[str, Any]], enrolled: Sequence[str]) -> dict[str, Any]:
        """Never raises. Returns the record stored at ``persona_cycle.meetings``."""
        self._run_reader = None
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
            self._mt5(ctx, observe, pid, enrolled)
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
            if type(pending) is int:
                ctx["record"]["not_fired"].append({"meeting_type": "MT-2", "reason": "no_optimisation_condition",
                                                   "pending_lesson_candidates": pending})
            else:  # name the missing input instead of carrying null
                ctx["record"]["not_fired"].append({"meeting_type": "MT-2", "reason": "pending_lesson_count_unavailable"})
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

    @staticmethod
    def _mt5_count(snapshots: Mapping[str, Mapping[str, Any]], tenant: str) -> int:
        count = 0
        for p in snapshots.values():
            if p.get("tenant_id") != tenant:
                continue
            meetings = (p.get("persona_cycle") or {}).get("meetings") or {}
            count += sum(1 for c in meetings.get("convened", []) if c.get("meeting_type") == "MT-5")
        return count

    def _mt5(self, ctx, observe, pid, enrolled):
        record = ctx["record"]
        persona = ctx["persona"]
        tenant = persona["tenant_id"]
        regime = observe.get("regime") or (ctx["packet"].get("persona_cycle") or {}).get("regime")
        governed_records, saw_proposed = _extract_governed_records(regime)
        enrolled_regimes = observe.get("enrolled_regimes")
        if isinstance(enrolled_regimes, Mapping):
            for _, er in enrolled_regimes.items():
                er_gov, er_prop = _extract_governed_records(er)
                saw_proposed = saw_proposed or er_prop
                for sid, r in er_gov.items():
                    governed_records.setdefault(sid, r)
        if not governed_records:
            reason = "proposed_regime_record_only" if saw_proposed else "no_governed_regime_record"
            record["not_fired"].append({"meeting_type": "MT-5", "reason": reason})
            return

        cur_session = str((regime or {}).get("session") or "")
        # Merge governed active records from same-session packets of other enrolled personas in this tenant.
        for p in ctx["snapshots"].values():
            if p.get("tenant_id") != tenant:
                continue
            p_reg = (p.get("persona_cycle") or {}).get("regime") or ((p.get("persona_cycle") or {}).get("observe_bundle") or {}).get("regime")
            if isinstance(p_reg, Mapping) and cur_session and str(p_reg.get("session") or "") == cur_session:
                p_gov, _ = _extract_governed_records(p_reg)
                for sid, r in p_gov.items():
                    governed_records.setdefault(sid, r)

        market_state = _resolve_market_state(regime, governed_records, cur_session or ctx["today"].isoformat())
        prior_entries = _collect_prior_governed_regimes(ctx["snapshots"], tenant, cur_session, regime)

        transitions: list[dict[str, Any]] = []
        # 1. Macro axis transition on governed RegimeState
        cur_macro = _extract_macro_state(regime, governed_records, market_state)
        prev_macro, prev_macro_session = "", ""
        for pr_session, pr, pr_records in reversed(prior_entries):
            cand = _extract_macro_state(pr, pr_records, pr.get("market_state") if isinstance(pr.get("market_state"), Mapping) else None)
            if cand:
                prev_macro, prev_macro_session = cand, pr_session
                break
        if not prev_macro and isinstance((regime or {}).get("macro"), Mapping):
            m_obj = regime["macro"]
            if str(m_obj.get("previous_record_state") or "active").lower() == "active":
                explicit_prev = str(m_obj.get("previous_state") or m_obj.get("from_state") or "").strip()
                if explicit_prev and explicit_prev.lower() != "proposed":
                    prev_macro = explicit_prev
                    prev_macro_session = str(m_obj.get("from_session") or m_obj.get("previous_session") or "prior")
        if prev_macro and cur_macro and prev_macro != cur_macro:
            transitions.append({
                "axis": "macro",
                "scope": "macro",
                "from_state": prev_macro,
                "to_state": cur_macro,
                "from_session": prev_macro_session or "prior",
                "to_session": cur_session or ctx["today"].isoformat(),
            })

        # 2. Primary axis transition of any enrolled pool strategy
        for sid, rec in sorted(governed_records.items()):
            primary_axis, cur_state = _primary_axis_and_state(sid, rec, market_state)
            if not cur_state:
                continue
            prev_state, prev_session, prev_regime_id = "", "", None
            for pr_session, pr, pr_records in reversed(prior_entries):
                if sid in pr_records:
                    prev_rec = pr_records[sid]
                    _, cand_state = _primary_axis_and_state(
                        sid, prev_rec, pr.get("market_state") if isinstance(pr.get("market_state"), Mapping) else None
                    )
                    if cand_state:
                        prev_state, prev_session = cand_state, pr_session
                        prev_regime_id = prev_rec.get("regime_id")
                        break
            if not prev_state and not prior_entries:
                if str(rec.get("previous_record_state") or "active").lower() == "active":
                    explicit_prev = str(rec.get("previous_state") or rec.get("from_state") or "").strip()
                    if explicit_prev and explicit_prev.lower() != "proposed":
                        prev_state = explicit_prev
                        prev_session = str(rec.get("from_session") or rec.get("previous_session") or f"prior:{cur_session}")
                if not prev_state and cur_session:
                    snap = rec.get("feature_snapshot") if isinstance(rec.get("feature_snapshot"), Mapping) else {}
                    ax_map = snap.get("axes") if isinstance(snap.get("axes"), Mapping) else {}
                    ax_det = ax_map.get(primary_axis) if isinstance(ax_map.get(primary_axis), Mapping) else {}
                    if ax_det.get("sessions_in_state") == 1 and str(ax_det.get("last_switch_session") or "") == cur_session:
                        prev_state = str(ax_det.get("previous_state") or f"pre_switch:{cur_state}")
                        prev_session = f"switch:{cur_session}"
            if prev_state and cur_state and prev_state != cur_state:
                transitions.append({
                    "axis": primary_axis,
                    "scope": f"strategy:{sid}",
                    "strategy_id": sid,
                    "from_state": prev_state,
                    "to_state": cur_state,
                    "from_session": prev_session or "prior",
                    "to_session": cur_session or ctx["today"].isoformat(),
                    "from_regime_id": prev_regime_id,
                    "to_regime_id": rec.get("regime_id"),
                })

        if not transitions:
            record["not_fired"].append({"meeting_type": "MT-5", "reason": "no_regime_transition"})
            return

        sig = [(t["axis"], t["scope"], t["from_state"], t["to_state"], t["from_session"]) for t in transitions]
        key = _key("MT-5", tenant, sig)
        enrolled_sorted = sorted(dict.fromkeys([*(enrolled or ()), pid]))
        chair_id = enrolled_sorted[self._mt5_count(ctx["snapshots"], tenant) % len(enrolled_sorted)]
        peers = [p for p in enrolled_sorted if p != chair_id][:3]
        participants = [
            {"persona_id": chair_id, "role": "chair"},
            *[{"persona_id": p, "role": "trading_peer"} for p in peers],
            {"persona_id": "persona-risk-officer", "role": "risk_reviewer"},
        ]
        exposures = _collect_pool_exposures(persona, observe, ctx["snapshots"], enrolled_sorted)
        case_file, case_items = _build_mt5_case_file(
            governed_records=governed_records,
            market_state=market_state,
            exposures=exposures,
            transitions=transitions,
            session=cur_session or ctx["today"].isoformat(),
        )
        trigger_desc = "regime_transition:" + ";".join(f"{t['axis']}:{t['from_state']}->{t['to_state']}" for t in transitions)
        topic = (
            f"Regime interpretation ({cur_session or ctx['today'].isoformat()}): "
            + "; ".join(f"{t['scope']} ({t['axis']}) {t['from_state']}->{t['to_state']}" for t in transitions)
        )
        self._convene(
            ctx,
            "MT-5",
            trigger_desc,
            topic,
            key,
            subject_ref=f"regime:{cur_session or ctx['today'].isoformat()}",
            participants=participants,
            case_file=case_file,
            evidence_refs=[item["item_id"] for item in case_items],
            metadata={
                "regime_transitions": transitions,
                "regime_records": governed_records,
                "market_state": market_state,
                "pool_exposures": exposures,
                "chair_persona_id": chair_id,
            },
        )

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


# ------------------------------------------------------------------- MT-5 ---
def pool_exposure_for(persona_id: str, observe_bundle: Mapping[str, Any] | None = None,
                      pool: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build one enrolled pool's exposure summary for the MT-5 case file."""
    bundle = observe_bundle if isinstance(observe_bundle, Mapping) else {}
    pool_obj = pool if isinstance(pool, Mapping) else (bundle.get("pool") if isinstance(bundle.get("pool"), Mapping) else {})
    subject = bundle.get("cycle_subject") if isinstance(bundle.get("cycle_subject"), Mapping) else {}
    observations = bundle.get("observations") if isinstance(bundle.get("observations"), list) else []
    raw_members = pool_obj.get("members") if isinstance(pool_obj.get("members"), list) else (
        bundle.get("members") if isinstance(bundle.get("members"), list) else []
    )
    members = [
        {
            "artifact_ref": str(m.get("artifact_ref") or m.get("member_id") or ""),
            "stage": str(m.get("stage") or "active"),
            "risk_weight": float(m.get("risk_weight") or 0.0),
        }
        for m in raw_members
        if isinstance(m, Mapping) and (m.get("artifact_ref") or m.get("member_id")) and m.get("stage") != "removed"
    ]
    gross_weight = round(sum(abs(m["risk_weight"]) for m in members), 6)
    net_weight = round(sum(m["risk_weight"] for m in members), 6)
    latest_obs = observations[-1] if observations and isinstance(observations[-1], Mapping) else {}
    gross_exposure = (
        float(bundle["gross_exposure"])
        if isinstance(bundle.get("gross_exposure"), (int, float))
        else (
            float(latest_obs["gross_notional"])
            if isinstance(latest_obs.get("gross_notional"), (int, float))
            else gross_weight
        )
    )
    net_exposure = (
        float(bundle["net_exposure"])
        if isinstance(bundle.get("net_exposure"), (int, float))
        else net_weight
    )
    families = list(
        bundle.get("strategy_families")
        or subject.get("families")
        or ([bundle["strategy_family"]] if bundle.get("strategy_family") else [])
    )
    return {
        "persona_id": str(persona_id),
        "pool_version": pool_obj.get("pool_version", bundle.get("pool_version")),
        "strategy_families": [str(f) for f in families if str(f or "").strip()],
        "underlyings": [str(u) for u in (bundle.get("underlyings") or subject.get("underlyings") or []) if str(u or "").strip()],
        "members": members,
        "gross_exposure": gross_exposure,
        "gross_limit": float(latest_obs["gross_limit"]) if isinstance(latest_obs.get("gross_limit"), (int, float)) else bundle.get("gross_limit"),
        "net_exposure": net_exposure,
        "drawdown": float(latest_obs["drawdown"]) if isinstance(latest_obs.get("drawdown"), (int, float)) else bundle.get("drawdown"),
    }


def _extract_governed_records(regime: Any) -> tuple[dict[str, dict[str, Any]], bool]:
    """Return ``(governed_active_records, saw_proposed)``. Never accepts missing or proposed records."""
    if not isinstance(regime, Mapping):
        return {}, False
    regime_state = str(regime.get("state") or "").strip().lower()
    regime_model = str(regime.get("model_status") or "").strip().lower()
    saw_proposed = regime_state == "proposed" or regime_model == "candidate"
    raw_records = regime.get("records")
    items: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(raw_records, Mapping):
        for k, v in raw_records.items():
            if isinstance(v, Mapping):
                items.append((str(k), v))
    elif isinstance(raw_records, Sequence) and not isinstance(raw_records, (str, bytes)):
        for idx, v in enumerate(raw_records):
            if isinstance(v, Mapping):
                items.append((str(v.get("strategy_id") or f"strategy-{idx + 1}"), v))
    for _, r in items:
        st = str(r.get("state") or "").strip().lower()
        ms = str(r.get("model_status") or "").strip().lower()
        if st == "proposed" or ms == "candidate":
            saw_proposed = True
    if regime.get("status") not in (None, "observed") or regime_state == "proposed" or regime_model == "candidate":
        return {}, saw_proposed
    governed: dict[str, dict[str, Any]] = {}
    for key_id, r in items:
        r_state = str(r.get("state") or "active").strip().lower()
        r_model = str(r.get("model_status") or "approved").strip().lower()
        if r_state == "proposed" or r_model == "candidate":
            saw_proposed = True
            continue
        if r_state != "active" or r_model != "approved":
            continue
        rid = str(r.get("regime_id") or "").strip()
        if not rid:
            continue
        sid = str(r.get("strategy_id") or key_id).strip()
        governed[sid] = dict(r, regime_id=rid, strategy_id=sid, state="active")
    return governed, saw_proposed


def _primary_axis_and_state(
    strategy_id: str,
    rec: Mapping[str, Any],
    market_state: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    snap = rec.get("feature_snapshot") if isinstance(rec.get("feature_snapshot"), Mapping) else {}
    profile = snap.get("profile") if isinstance(snap.get("profile"), Mapping) else {}
    axis = str(rec.get("primary_axis") or profile.get("primary_axis") or "").strip()
    horizon = str(rec.get("horizon") or profile.get("horizon") or "").strip()
    explicit_state = str(rec.get("primary_state") or rec.get("axis_state") or "").strip()
    if explicit_state:
        return (axis or "primary"), explicit_state
    axes = snap.get("axes") if isinstance(snap.get("axes"), Mapping) else {}
    if axis and isinstance(axes.get(axis), Mapping):
        ax_det = axes[axis]
        if ax_det.get("available", True) and ax_det.get("state"):
            return axis, str(ax_det["state"]).strip()
    if axis and isinstance(market_state, Mapping):
        ms_axes = market_state.get("axes") if isinstance(market_state.get("axes"), Mapping) else {}
        ms_ax = ms_axes.get(axis)
        if isinstance(ms_ax, Mapping):
            h_det = ms_ax.get(horizon) if horizon and isinstance(ms_ax.get(horizon), Mapping) else ms_ax
            if isinstance(h_det, Mapping) and h_det.get("available", True) and h_det.get("state"):
                return axis, str(h_det["state"]).strip()
    cls = str(rec.get("regime_class") or "").strip()
    if cls in {"regime_transition", "mixed"}:
        return (axis or "primary"), ""
    return (axis or "primary"), cls


def _extract_macro_state(
    regime: Mapping[str, Any] | None,
    governed_records: Mapping[str, Mapping[str, Any]],
    market_state: Mapping[str, Any] | None = None,
) -> str:
    if not governed_records or not isinstance(regime, Mapping):
        return ""
    macro = regime.get("macro")
    if isinstance(macro, Mapping):
        if str(macro.get("state") or "").strip().lower() not in {"", "proposed", "unknown"} and macro.get("available", True):
            return str(macro["state"]).strip()
    elif isinstance(macro, str) and macro.strip().lower() not in {"", "proposed", "unknown"}:
        return macro.strip()
    ms = market_state if isinstance(market_state, Mapping) else (
        regime.get("market_state") if isinstance(regime.get("market_state"), Mapping) else {}
    )
    if str(ms.get("model_status") or "approved").lower() == "approved" and str(ms.get("state") or "current").lower() not in {"proposed", "invalidated"}:
        m_ax = (ms.get("axes") or {}).get("macro") if isinstance(ms.get("axes"), Mapping) else {}
        if isinstance(m_ax, Mapping) and m_ax.get("available", True) and str(m_ax.get("state") or "").strip():
            return str(m_ax["state"]).strip()
    for rec in governed_records.values():
        snap = rec.get("feature_snapshot") if isinstance(rec.get("feature_snapshot"), Mapping) else {}
        axes = snap.get("axes") if isinstance(snap.get("axes"), Mapping) else {}
        m_ax = axes.get("macro")
        if isinstance(m_ax, Mapping) and m_ax.get("available", True) and str(m_ax.get("state") or "").strip():
            return str(m_ax["state"]).strip()
        env_m = str(snap.get("macro_environment") or "").strip()
        if env_m and env_m.lower() not in {"unknown", "proposed"}:
            return env_m
    return ""


def _resolve_market_state(
    regime: Mapping[str, Any] | None,
    governed_records: Mapping[str, Mapping[str, Any]],
    session: str,
) -> dict[str, Any]:
    ms = (regime or {}).get("market_state") if isinstance(regime, Mapping) else None
    if isinstance(ms, Mapping) and ms:
        out = dict(ms)
        out.setdefault("market", "TW")
        out.setdefault("session", str((regime or {}).get("session") or session))
        out.setdefault("model_status", "approved")
        return out
    axes: dict[str, Any] = {}
    for rec in governed_records.values():
        snap = rec.get("feature_snapshot") if isinstance(rec.get("feature_snapshot"), Mapping) else {}
        for ax_name, ax_val in (snap.get("axes") or {}).items() if isinstance(snap.get("axes"), Mapping) else ():
            axes.setdefault(str(ax_name), ax_val)
        axis_name, axis_state = _primary_axis_and_state(str(rec.get("strategy_id") or ""), rec)
        if axis_name and axis_state and axis_name not in axes:
            axes[axis_name] = {"available": True, "state": axis_state}
    macro_state = _extract_macro_state(regime, governed_records)
    if macro_state and "macro" not in axes:
        axes["macro"] = {"available": True, "state": macro_state}
    return {
        "market": "TW",
        "session": session,
        "model_status": "approved",
        "state": "current",
        "axes": axes,
    }


def _collect_prior_governed_regimes(
    snapshots: Mapping[str, Mapping[str, Any]],
    tenant: str,
    cur_session: str,
    regime: Mapping[str, Any] | None,
) -> list[tuple[str, Mapping[str, Any], dict[str, dict[str, Any]]]]:
    candidates: list[tuple[str, int, Mapping[str, Any], dict[str, dict[str, Any]]]] = []
    idx = 0
    if isinstance(regime, Mapping):
        for k in ("previous", "previous_regime"):
            prev_reg = regime.get(k)
            if isinstance(prev_reg, Mapping):
                gov, _ = _extract_governed_records(prev_reg)
                if gov:
                    p_sess = str(prev_reg.get("session") or "")
                    if not (cur_session and p_sess and p_sess >= cur_session):
                        idx += 1
                        candidates.append((p_sess, idx, prev_reg, gov))
    for p in snapshots.values():
        if p.get("tenant_id") != tenant:
            continue
        p_cycle = p.get("persona_cycle") or {}
        p_reg = p_cycle.get("regime") or (p_cycle.get("observe_bundle") or {}).get("regime")
        if not isinstance(p_reg, Mapping):
            continue
        gov, _ = _extract_governed_records(p_reg)
        if not gov:
            continue
        p_sess = str(p_reg.get("session") or "")
        if cur_session and p_sess and p_sess >= cur_session:
            continue
        idx += 1
        candidates.append((p_sess, idx, p_reg, gov))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [(s, r, g) for s, _, r, g in candidates]


def _collect_pool_exposures(
    persona: Mapping[str, Any],
    observe: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    enrolled: Sequence[str],
) -> dict[str, dict[str, Any]]:
    tenant = persona["tenant_id"]
    pid = persona["persona_id"]
    supplied = observe.get("pool_exposures") if isinstance(observe.get("pool_exposures"), Mapping) else {}
    exposures: dict[str, dict[str, Any]] = {}
    for epid in enrolled:
        if epid in supplied and isinstance(supplied[epid], Mapping):
            exposures[epid] = pool_exposure_for(epid, supplied[epid])
            continue
        if epid == pid:
            exposures[epid] = pool_exposure_for(epid, observe)
            continue
        peer_bundle: Mapping[str, Any] = {}
        for p in snapshots.values():
            if p.get("tenant_id") == tenant and epid in p.get("persona_ids", []):
                ob = (p.get("persona_cycle") or {}).get("observe_bundle")
                if isinstance(ob, Mapping) and ob:
                    peer_bundle = ob
        exposures[epid] = pool_exposure_for(epid, peer_bundle)
    return exposures


def _build_mt5_case_file(
    *,
    governed_records: Mapping[str, Mapping[str, Any]],
    market_state: Mapping[str, Any],
    exposures: Mapping[str, Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]],
    session: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    for sid, rec in sorted(governed_records.items()):
        primary_axis, primary_state = _primary_axis_and_state(sid, rec, market_state)
        rid = str(rec["regime_id"])
        items.append({
            "item_id": f"facts:regime_record:{rid}",
            "group": "facts",
            "kind": "regime_record",
            "source_owner": "registry",
            "source_id": rid,
            "excerpt": json.dumps(rec, sort_keys=True, ensure_ascii=False),
            "regime_record": dict(rec),
            "subject_fields": {
                "strategy_id": sid,
                "regime_id": rid,
                "regime_class": str(rec.get("regime_class") or ""),
                "primary_axis": primary_axis,
                "primary_state": primary_state,
            },
        })
    ms_id = str(
        market_state.get("market_state_id")
        or f"{market_state.get('market', 'TW')}:{market_state.get('session') or session}:{market_state.get('model_set_version', 'approved')}:r{market_state.get('revision', 1)}"
    )
    items.append({
        "item_id": f"facts:market_state:{ms_id}",
        "group": "facts",
        "kind": "market_state",
        "source_owner": "registry",
        "source_id": ms_id,
        "excerpt": json.dumps(dict(market_state), sort_keys=True, ensure_ascii=False),
        "market_state": dict(market_state),
        "subject_fields": {
            "market": str(market_state.get("market") or "TW"),
            "session": str(market_state.get("session") or session),
            "market_state_id": ms_id,
        },
    })
    for epid, exp in sorted(exposures.items()):
        items.append({
            "item_id": f"facts:pool_exposure:{epid}",
            "group": "facts",
            "kind": "pool_exposure",
            "source_owner": "persona",
            "source_id": epid,
            "excerpt": json.dumps(dict(exp), sort_keys=True, ensure_ascii=False),
            "pool_exposure": dict(exp),
            "subject_fields": {
                "persona_id": epid,
                "gross_exposure": exp.get("gross_exposure"),
                "net_exposure": exp.get("net_exposure"),
            },
        })
    case_file = {
        "items": items,
        "regime_records": {k: dict(v) for k, v in governed_records.items()},
        "market_state": dict(market_state),
        "pool_exposures": {k: dict(v) for k, v in exposures.items()},
        "transitions": [dict(t) for t in transitions],
    }
    return case_file, items


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
