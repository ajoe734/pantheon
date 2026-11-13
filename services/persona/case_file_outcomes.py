"""Persona-owned outcome review of cited case-file lessons, paper only.

Reconciles the existing persisted cycle packets, with no model or second
scheduler. Paired utility is the sum of net-of-costs daily returns under the
frozen before/after pool weights. This measures the decision, not causal credit
for an individual lesson. Unsupported/staged decisions remain confounded.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote

from services.persona.persona_cycle_controller import PersonaCycleController, read_packets

STATE_KEY = "case_file_outcomes"
OBSERVATION_TRADING_DAYS = 10
MISSING_OUTCOME_DEADLINE_DAYS = 30
CRITERION = "frozen_pool_net_of_costs_daily_return_sum"
ACTOR = "persona-svc"


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("outcome_timestamp_requires_utc")
    return parsed


def _id(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class CaseFileOutcomes:
    def __init__(self, persona_owner, pool_store, *, now: Callable = lambda: datetime.now(timezone.utc), sender=None):
        self.personas, self.pool, self.now = persona_owner, pool_store, now
        self.sender = sender or self._send

    def _mutate(self, persona_id, reduce):
        from services.persona.write_owner import PersonaConcurrentUpdate
        for _ in range(4):
            result = {}
            def update(persona):
                if persona.lifecycle_state != "paper_owner" or persona.status != "active" or not persona.tenant_id:
                    raise ValueError("active_paper_owner_required")
                state = copy.deepcopy(persona.metadata.get(STATE_KEY) or {"decisions": {}, "outbox": {}})
                result.update(reduce(persona, state))
                return {STATE_KEY: state}
            try:
                self.personas.try_metadata_cas(persona_id, guard=lambda p: True,
                                              metadata_updates=update, actor_id=ACTOR)
                return result
            except PersonaConcurrentUpdate:
                continue
        raise ValueError("concurrent_case_file_outcome_update")

    @staticmethod
    def _owned(packet, persona):
        return (packet.get("tenant_id") == persona.tenant_id and packet.get("persona_ids") == [persona.persona_id]
                and packet.get("environment") == "paper"
                and packet.get("persona_cycle", {}).get("phase") == "terminal")

    def reconcile(self, persona_id: str, packets: dict) -> dict:
        """Capture cited ids before evaluation; freeze one result and retry its delivery."""
        def reduce(persona, state):
            owned = {pid: p for pid, p in packets.items() if self._owned(p, persona)}
            for pid, packet in owned.items():
                cycle = packet["persona_cycle"]
                for index, decision in enumerate(cycle.get("decisions") or []):
                    cited = list(dict.fromkeys(decision.get("cited_case_file_item_ids") or []))
                    if not cited:
                        continue
                    identity = "case-decision-" + _id([persona.tenant_id, persona_id, pid, index])
                    if identity in state["decisions"]:
                        continue
                    case = (cycle.get("case_files") or {}).get(decision.get("case_file_id")) or {}
                    items = {i["item_id"]: i for g in case.get("groups", {}).values() for i in g["items"]}
                    lessons = {}
                    for item_id in cited:
                        item = items.get(item_id) or {}
                        if item.get("source_owner") == "memory" and item.get("source_id"):
                            layer = "institutional" if item.get("memory_layer") in {"institutional", "negative_memory"} else "persona"
                            target = lessons.setdefault((layer, item["source_id"]), [])
                            target.append(item_id)
                    row = {"decision_id": identity, "packet_id": pid, "case_file_id": decision.get("case_file_id"),
                           "cited_item_ids": cited, "lessons": [{"layer": k[0], "entry_id": k[1], "item_ids": v}
                                                                for k, v in lessons.items()],
                           "decided_at": packet["created_at"], "criterion": CRITERION,
                           "window_trading_days": OBSERVATION_TRADING_DAYS, "status": "pending",
                           "harness_version": cycle.get("harness_version"), "checks": []}
                    subject = case.get("subject") or {}
                    if (set(cited) - items.keys() or case.get("digest") != decision.get("case_file_digest")
                            or subject.get("tenant_id") != persona.tenant_id or subject.get("persona_id") != persona_id):
                        row["checks"].append("case_file_binding_invalid")
                    before = (cycle.get("observe_bundle") or {}).get("pool") or {}
                    receipt = (cycle.get("risk_result") or {}).get("receipt_ref", "")
                    # Bind to the exact owner version, never whatever pool happens to be current.
                    prefix = f"/api/personas/{quote(persona_id, safe='')}/strategy-pool/versions/"
                    try:
                        if not receipt.startswith(prefix):
                            raise ValueError("risk_receipt_missing")
                        after = self.pool.version(persona_id, persona.tenant_id, int(receipt[len(prefix):]))
                        canonical_before = self.pool.version(persona_id, persona.tenant_id, before["pool_version"])
                        if (before != canonical_before or after["pool_version"] != before["pool_version"] + 1
                                or after.get("decision", {}).get("status") != "applied"
                                or decision.get("status") != "deterministic_risk_only"
                                or decision.get("kind") not in {"remove", "reweight"}):
                            raise ValueError("decision_not_applied_by_owner")
                        row.update(before=canonical_before, after=after)
                    except (ValueError, KeyError, TypeError):
                        row["checks"].append("applied_pool_decision_unavailable")
                    state["decisions"][identity] = row
            for identity, row in state["decisions"].items():
                if row["status"] == "observed":
                    continue
                result = self._evaluate(row, owned)
                if result is None:
                    continue
                row.update(status="observed", **result)
                marks = [{"entry_id": lesson["entry_id"], "layer": lesson["layer"], "mark": {
                    "decision_id": identity, "case_file_id": row["case_file_id"],
                    "cited_item_ids": lesson["item_ids"], "outcome": result["outcome"],
                    "reason": result["reason"], "evidence_refs": result["evidence_refs"],
                    "window_end": result["window_end"]}} for lesson in row["lessons"]]
                if marks:
                    state["outbox"][identity] = {"status": "pending", "attempts": 0, "payload": {
                        "source_event_type": "case_file_usefulness", "source_event_id": identity,
                        "write_authority": ACTOR, "tenant_id": persona.tenant_id,
                        "sponsor_persona_id": persona_id, "marks": marks}}
            return {"decisions": copy.deepcopy(state["decisions"])}
        result = self._mutate(persona_id, reduce)
        result["delivery"] = self.deliver(persona_id)
        return result

    def _evaluate(self, row, packets):
        decided = _time(row["decided_at"])
        future = sorted((p for p in packets.values() if decided < _time(p["created_at"]) <= self.now()),
                        key=lambda p: (p["created_at"], p["packet_id"]))
        # Calendar comes from owner-reported trading dates, never inferred weekdays.
        calendar = set()
        for packet in future:
            for observation in packet["persona_cycle"].get("observe_bundle", {}).get("observations", []):
                try:
                    day = date.fromisoformat(observation["date"])
                    if observation.get("source_ref") and decided.date() < day <= _time(packet["created_at"]).date():
                        calendar.add(day.isoformat())
                except (KeyError, TypeError, ValueError):
                    continue
        dates = sorted(calendar)
        if len(dates) < row["window_trading_days"] and self.now() < decided + timedelta(days=MISSING_OUTCOME_DEADLINE_DAYS):
            return None
        days = dates[:row["window_trading_days"]]
        reasons = list(row["checks"])
        refs = [row["packet_id"]]
        if len(days) < row["window_trading_days"]:
            reasons.append("outcome_window_missing")
        # Freeze the earliest packet that reports the end of the first window.
        snapshot = next((p for p in future if days and any(o.get("date") == days[-1] and o.get("source_ref")
                        for o in p["persona_cycle"].get("observe_bundle", {}).get("observations", []))), None)
        delta = None
        if snapshot is None:
            reasons.append("paired_outcome_missing")
        else:
            refs.append(snapshot["packet_id"])
            bundle = snapshot["persona_cycle"].get("observe_bundle", {})
            window_packets = [p for p in future if p["created_at"] <= snapshot["created_at"]]
            if (bundle.get("persona_id") != packets[row["packet_id"]]["persona_ids"][0]
                    or bundle.get("unavailable") or bundle.get("incident_refs")
                    or bundle.get("incidents", {}).get("status") != "observed"
                    or snapshot["persona_cycle"].get("harness_version") != row["harness_version"]
                    or bundle.get("confounders")):
                reasons.append("outcome_confounded_or_unresolved")
            if any(p["persona_cycle"].get("harness_version") != row["harness_version"]
                   or p["persona_cycle"].get("observe_bundle", {}).get("incident_refs")
                   or p["persona_cycle"].get("observe_bundle", {}).get("confounders")
                   for p in window_packets):
                reasons.append("intervening_window_confounder")
            if not reasons:
                try:
                    from services.control_plane.persona.pool_policy import dated_series
                    before, after = row["before"]["members"], row["after"]["members"]
                    members = {m["artifact_ref"]: m for m in before + after if m["stage"] != "removed" and m["risk_weight"] > 0}
                    series = {ref: dated_series(bundle.get("books", {}).get(ref), member["checksum"])
                              for ref, member in members.items()}
                    refs += sorted({bundle["books"][ref]["source_ref"] for ref in members})
                    def utility(pool):
                        return sum(m["risk_weight"] * series[m["artifact_ref"]][day] for m in pool
                                   if m["stage"] != "removed" and m["risk_weight"] > 0 for day in days)
                    delta = utility(after) - utility(before)
                except (ValueError, KeyError, TypeError):
                    reasons.append("paired_virtual_book_missing_or_invalid")
        outcome = "confounded" if reasons else "helped" if delta > 0 else "hurt" if delta < 0 else "no_effect"
        return {"outcome": outcome, "delta": None if reasons else delta,
                "reason": ";".join(sorted(set(reasons))) if reasons else CRITERION,
                "evidence_refs": refs, "window_end": days[-1] if days else self.now().date().isoformat()}

    def deliver(self, persona_id, *, limit=4):
        state = self.personas.get(persona_id).metadata.get(STATE_KEY, {})
        report = []
        for identity, intent in [i for i in state.get("outbox", {}).items() if i[1]["status"] != "delivered"][:limit]:
            try:
                receipt = self.sender(copy.deepcopy(intent["payload"]))
                expected = [(m["entry_id"], m["layer"], m["mark"]["decision_id"], m["mark"]["outcome"])
                            for m in intent["payload"]["marks"]]
                actual = [(m["entry_id"], m["layer"], m["decision_id"], m["outcome"]) for m in receipt.get("marks", [])]
                if (actual != expected or receipt.get("source_event_id") != identity
                        or receipt.get("sponsor_persona_id") != persona_id
                        or receipt.get("source_event_type") != "case_file_usefulness"):
                    raise ValueError("usefulness_receipt_binding_mismatch")
                error = None
            except Exception as exc:
                receipt, error = None, type(exc).__name__
            def update(persona, current):
                stored = current["outbox"][identity]
                if stored["status"] != "delivered":
                    stored.update(status="pending" if error else "delivered", error=error,
                                  receipt=receipt, attempts=stored["attempts"] + 1)
                return {"status": stored["status"], "decision_id": identity}
            report.append(self._mutate(persona_id, update))
        return report

    @staticmethod
    def _send(payload):
        # Uses existing configured Persona -> Memory credentials; never mints one.
        from services.persona.harness_adoption import HarnessAdoption
        return HarnessAdoption._send("memory", payload)


class OutcomeTrackingController(PersonaCycleController):
    """Same cron reconciliation entry point, followed by bounded owner review."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = CaseFileOutcomes(self.owners.persona_owner, self.owners.pool_store)

    def reconcile_once(self):
        report = super().reconcile_once()
        packets = read_packets(self.path)
        for persona in self.owners.personas():
            if persona.get("lifecycle_state") == "paper_owner" and persona.get("status", "active") == "active":
                try:
                    result = self.outcomes.reconcile(persona["persona_id"], packets)
                    if any(d["status"] == "pending" for d in result["delivery"]):
                        raise ValueError("memory_usefulness_delivery_pending")
                except Exception as exc:
                    report["failures"].append({"persona_id": persona["persona_id"],
                                               "reason": "case_file_outcome:" + type(exc).__name__})
        return report
