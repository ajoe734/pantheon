"""Pool evolution inside the existing consumer tick; no persona lifecycle writes.

Trading days come from the member's observed virtual book, never a weekday
calendar. Proposal IDs survive pool version changes and process restarts; the
proposal owner remains responsible for concurrency and cooldown admission.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping

from services.evolution.approved_baselines import compare_member_book, resolve_member_baseline
from services.evolution.auto_consumer import build_retire_proposal, evaluate_paper_decay

POOL_SCOPE = "pool_member"
VARIANTS = 50
FAMILY_LIMIT = 500


def member_days(member, book, *, today=None):
    """Reject unbound, undated or future observations before using a cadence."""
    if book.get("checksum") != member["checksum"] or not book.get("source_ref"):
        raise ValueError("member_book_identity_or_source_missing")
    days = book.get("dates") or []
    parsed = [date.fromisoformat(d) for d in days]
    if not parsed or parsed != sorted(set(parsed)):
        raise ValueError("member_trading_days_missing_or_unordered")
    today = today or datetime.now(timezone.utc).date()
    if parsed[-1] > today:
        raise ValueError("future_member_observation")
    joined = date.fromisoformat(member["joined_at"][:10])
    return [d.isoformat() for d in parsed if d > joined]


def context(pool, member):
    return {"persona_id": pool["persona_id"], "pool_version": pool["pool_version"],
            "artifact_ref": member["artifact_ref"], "checksum": member["checksum"],
            "joined_at": member["joined_at"]}


def proposal(pool, member, entry, action, period):
    ctx = context(pool, member)
    identity = [pool["tenant_id"], pool["persona_id"], member["artifact_ref"],
                member["checksum"], member["joined_at"], action, period]
    decision_id = "evd-pool-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
    return {"decision_id": decision_id, "tenant_id": pool["tenant_id"],
            "target_type": "candidate_artifact", "target_id": member["artifact_ref"],
            "target_version": entry["version"], "target_stage": "paper", "risk_level": "low",
            "persona_id": pool["persona_id"], "action_type": action,
            "created_by_role": "evolution_controller", "created_by_id": "evolution-auto-consumer",
            "rationale": f"Paper pool member {action}: {period}",
            "evidence_refs": [{"ref_type": "telemetry_summary", "ref_id": decision_id}],
            "metadata": {"discovery_auto_scope": True, POOL_SCOPE: ctx, "variants": VARIANTS}}


@dataclass
class ScheduleReport:
    proposed: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class PoolEvolutionScheduler:
    def __init__(self, *, owner, decisions, evidence, ledger, clock=None):
        self.owner, self.decisions, self.evidence, self.ledger = owner, decisions, evidence, ledger
        self.clock = clock or (lambda: datetime.now(timezone.utc).date())

    def _propose(self, body, report):
        try:
            self.decisions.propose(body)
        except RuntimeError as exc:
            if " -> 409" not in str(exc):
                raise
            # A single-active or cooldown refusal is not evidence of execution.
            report.existing.append(body["decision_id"])
            return
        report.proposed.append(body["decision_id"])
        self.evidence.record({"event": "pool_evolution_proposed", "proposal": body})

    def tick(self):
        report = ScheduleReport()
        for pool in self.owner.pools():
            pid = pool["persona_id"]
            try:
                # Reconcile an append whose publication was interrupted, including
                # research trial admission. Publishing the same version is idempotent.
                self.owner.publish(pool)
                books = self.owner.books(pool)
                for member in pool["members"]:
                    if member["stage"] == "removed":
                        continue
                    key = f"{pid}:{member['artifact_ref']}"
                    try:
                        self._member(pool, member, books, key, report)
                    except Exception as exc:
                        report.errors[key] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                report.errors[pid] = f"{type(exc).__name__}: {exc}"
        self.evidence.record({"event": "pool_evolution_tick", **asdict(report)})
        return report

    def _member(self, pool, member, books, key, report):
        entry = self.owner.entry(member["artifact_ref"])
        baseline = resolve_member_baseline(tenant_id=pool["tenant_id"], persona_id=pool["persona_id"],
            artifact_ref=member["artifact_ref"], checksum=member["checksum"], entry=entry)
        if baseline["status"] != "approved":
            report.skipped[key] = "no_approved_baseline:" + baseline["reason"]
            return
        book = books.get(member["artifact_ref"], {})
        days = member_days(member, book, today=self.clock())
        comparison = compare_member_book(baseline, book)
        if comparison["status"] != "compared":
            report.skipped[key] = comparison["status"] + ":" + comparison.get("reason", "")
            return
        if not days:
            report.skipped[key] = "no_post_admission_data"
            return
        if member["stage"] == "trial":
            self._compete(pool, member, books, days, report, key)
            return
        metric = next(b for b in (book, book.get("gate_basis", {}), book.get("net_of_costs", {}))
                      if b.get("basis") == baseline["return_basis"])
        self.ledger.append_if_new({"tenant_id": pool["tenant_id"], "persona_id": pool["persona_id"],
            "artifact_id": member["artifact_ref"], "as_of": days[-1],
            "sharpe_ratio": metric["sharpe_ratio"], "max_drawdown": metric["max_drawdown"]})
        verdict = evaluate_paper_decay(baseline_sharpe=baseline["oos_sharpe"],
            baseline_max_drawdown=baseline["oos_max_drawdown"],
            paper_windows=self.ledger.windows(pool["tenant_id"], pool["persona_id"], member["artifact_ref"]))
        if verdict.decayed:
            body = proposal(pool, member, entry, "retire", "decay")
            decay = build_retire_proposal(decision_id=body["decision_id"], tenant_id=pool["tenant_id"],
                persona_id=pool["persona_id"], artifact_id=member["artifact_ref"],
                artifact_version=entry["version"], verdict=verdict)
            body["metadata"].update(decay["metadata"])
            self._propose(body, report)
            return
        if len(days) >= 20:
            self._propose(proposal(pool, member, entry, "revalidate", f"trading-window:{len(days)//20}"), report)
        family = (entry["metadata"]["strategy_artifact"]).get("strategy_id")
        frequency = ((entry["metadata"]["strategy_artifact"]).get("parameters") or {}).get("bar_frequency")
        # Daily/undeclared callers keep the legacy one-argument contract.
        state = self.owner.family_state(family, frequency) if frequency else self.owner.family_state(family)
        if state["trials"] >= FAMILY_LIMIT:
            report.skipped[key] = "family_tried"
            return
        if state["trials"] + VARIANTS > FAMILY_LIMIT:
            report.skipped[key] = "family_budget_below_50"
            return
        kind = entry["metadata"]["strategy_artifact"]["strategy_logic"]["kind"]
        action = "mutate" if kind == "factor_rule_v1" else "retrain"
        week = date.fromisoformat(days[-1]).isocalendar()
        self._propose(proposal(pool, member, entry, action, f"week:{week.year}-{week.week}"), report)

    def _compete(self, pool, child, books, days, report, key):
        if not child.get("parent_ref") or len(days) < 60:
            report.skipped[key] = "child_requires_60_post_admission_trading_days"
            return
        parent = next((m for m in pool["members"] if m["artifact_ref"] == child["parent_ref"]
                       and m["stage"] == "active"), None)
        if parent is None:
            report.skipped[key] = "parent_not_active"
            return
        receipt = self.owner.compete(pool, child, books)
        self.evidence.record({"event": "pool_competition", "persona_id": pool["persona_id"],
                              "child": child["artifact_ref"], "receipt": receipt})
