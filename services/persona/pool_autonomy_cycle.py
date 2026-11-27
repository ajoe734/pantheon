"""Daily Observe-Orient-Decide-Act-Learn cycle of one persona over its strategy pool.

Runs inside the existing persona daily-cycle consumer (``PersonaCycleController``)
and fills that cycle's OODA record; there is no second schedule. The decision is
deterministic policy over the observed pool health, never a model output, and
every action goes through an existing owner route, paper only:

  open_demand      research owner  POST /api/research/persona-demands
  admit_trial      consultation owner opens a risk persona panel (trial_admission, via
                   consult_triggers) and the cycle does not admit; a LATER cycle admits
                   through persona owner POST .../strategy-pool/decisions (admit) only
                   with the verified published memo. Oppose / missing / ambiguous risk
                   answer queues an operator decision (evolution proposal) instead.
  reweight/remove  persona owner   POST .../strategy-pool/decisions (reweight; the
                                   owner removes members past its drawdown rule)
  publish          after the act step, an applied pool version that changes live membership, a stage or a
                   weight beyond the policy tolerance is published through the BFF publish route (the
                   existing publisher + owner chain); a membership change proceeds only with the verified
                   envelope_publication memo or, when that panel escalates, an operator approval of the one
                   operator decision the cycle queues for it (evolution proposals, as for trial admission); an
                   operator rejection keeps the previous envelope. An unchanged re-weight publishes nothing; a failed publish is
                   recorded on the cycle record and retried by the next cycle (compared with the last
                   confirmed publication).
  propose_mutate   evolution owner proposal (same builder as the pool scheduler)
  propose_widen    staged only: no permitted-universe owner exists yet, so no
                   authority to widen is inferred
  no_action        reason recorded

Outcomes are written to the persona's own memory (``pool_outcome``) with
``tried_family:<id>`` tags; the next demand excludes those families.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from services.persona.pool_publish_port import PoolPublishPort

DECISIONS = ("open_demand", "admit_trial", "reweight", "remove", "propose_mutate", "propose_widen", "no_action")
MEASURE_BY_REASON = {"low_risk_budget_utilisation": "gross_utilisation",
                     "too_few_independent_bets": "effective_bets"}
# Only these outcomes make a family "tried"; a pending or operator-held admission is not a failure.
TRIED_OUTCOMES = ("admission_refused", "removed")
WIDEN_AFTER_TRIED_FAMILIES = 3
TRIAL_REVIEW_DAYS = 20


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def short_measures(pool: Mapping[str, Any], health: Mapping[str, Any]) -> list[str]:
    """Which health measure is short. Unknown inputs are not a shortage of strategies."""
    live = [m for m in pool.get("members", []) if m["stage"] != "removed"]
    if not live:
        return ["no_live_members"]
    return [MEASURE_BY_REASON[r] for r in health.get("demand_reasons", []) if r in MEASURE_BY_REASON]


def decide(*, pool, health, open_demands, candidates, tried, now_day) -> dict[str, Any]:
    """Pure policy: one decision per day with its reasons."""
    members = pool.get("members", [])
    live = [m for m in members if m["stage"] != "removed"]
    short = short_measures(pool, health)
    fresh = [c for c in candidates if c["artifact_ref"] not in {m["artifact_ref"] for m in members}]
    if fresh and (short or not live):
        # Admission never depends on idle capital; the owner still applies every criterion.
        return {"kind": "admit_trial", "reason": "candidate_available_while_pool_short:" + ",".join(short or ["no_live_members"]),
                "candidate": fresh[0], "short_measures": short}
    if short:
        open_measures = {d["short_measure"] for d in open_demands if d["status"] == "open"}
        missing = [m for m in short if m not in open_measures]
        if missing:
            return {"kind": "open_demand", "reason": "pool_short:" + ",".join(missing), "short_measure": missing[0],
                    "short_measures": short}
        if "effective_bets" in short and len(tried) >= WIDEN_AFTER_TRIED_FAMILIES:
            return {"kind": "propose_widen", "reason": f"demand_open_and_{len(tried)}_families_tried",
                    "short_measures": short}
    trials = [m for m in live if m["stage"] == "trial"
              and (_parse(now_day) - _parse(m["joined_at"])).days >= TRIAL_REVIEW_DAYS]
    if trials:
        return {"kind": "reweight", "reason": "trial_members_past_review_window", "short_measures": short}
    active = [m for m in live if m["stage"] == "active"]
    if active and not short and not health.get("demand_reasons"):
        return {"kind": "propose_mutate", "reason": "pool_healthy_explore_variant", "member": active[0],
                "short_measures": []}
    if short:
        return {"kind": "no_action", "reason": "demand_already_open_waiting_for_candidate", "short_measures": short}
    return {"kind": "no_action", "reason": "health_inputs_unknown_or_pool_sufficient:"
            + ",".join(health.get("demand_reasons", []) or ["sufficient"]), "short_measures": []}


class PoolAutonomyCycle:
    def __init__(self, ports: Any, *, now: Callable[[], str] = _now) -> None:
        self.ports, self.now = ports, now

    def run(self, persona: Mapping[str, Any], observe: Mapping[str, Any], packet_id: str) -> dict[str, Any]:
        """Run one cycle. Never raises: a failure is recorded so other personas continue."""
        pid = persona["persona_id"]
        base = {"persona_id": pid, "packet_id": packet_id, "decision_kinds": DECISIONS}
        pool = observe.get("pool")
        if not pool or "strategy_pool" in observe.get("unavailable", []):
            return {**base, "status": "skipped", "reason": "no_strategy_pool"}
        try:
            return {**base, **self._run(persona, observe, pool, packet_id)}
        except Exception as exc:  # noqa: BLE001 - isolation between personas
            return {**base, "status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}

    def _run(self, persona, observe, pool, packet_id) -> dict[str, Any]:
        ports, pid = self.ports, persona["persona_id"]
        health = observe.get("pool_health") or {}
        # Observe. The risk reweight earlier in the same cycle may have appended a
        # version, so decide against the owner's current pool, not the snapshot.
        observed_live = {m["artifact_ref"] for m in pool["members"] if m["stage"] != "removed"}
        pool = ports.current_pool(persona) or pool
        # Members the deterministic risk owner removed earlier in this same cycle are
        # outcomes too: persist them to this persona's memory so the demand and the
        # candidate filter below exclude their families.
        pre_learned = [{"outcome": "removed", "family": ports.family(persona, ref), "artifact_ref": ref,
                        "detail": "own_high_water_drawdown"}
                       for ref in sorted(observed_live - {m["artifact_ref"] for m in pool["members"] if m["stage"] != "removed"})]
        memory_errors: list[str] = []
        self._learn(persona, pre_learned, packet_id, memory_errors)
        demands = ports.reconcile_demands(persona, pool["members"])
        candidates = ports.candidates(persona, pool)
        failed_families = ({str(f).lower() for f in ports.recall_tried_families(persona)}
                           | {str(e["family"]).lower() for e in pre_learned if e.get("family")})
        member_families = {str(f).lower() for f in (ports.family(persona, m["artifact_ref"]) for m in pool["members"]) if f}
        tried = sorted(failed_families | member_families)
        orient = {"pool_version": pool["pool_version"], "health": health,
                  "short_measures": short_measures(pool, health), "open_demands": [d["demand_id"] for d in demands if d["status"] == "open"],
                  "candidate_count": len(candidates), "tried_families": tried}
        # Decide
        decision = decide(pool=pool, health=health, open_demands=demands,
                          candidates=[c for c in candidates if not c.get("family") or str(c["family"]).lower() not in failed_families],
                          tried=tried, now_day=self.now())
        # Act
        receipts, learned = self._act(persona, pool, observe, health, decision, tried, packet_id)
        # Discovery: each open demand drives the existing persona strategy discovery (design 6.6)
        discovery = self._discover(persona, pool)
        # Publish: every applied pool version that changes what runs goes through the owner chain (R6).
        publication = self._publish(persona, packet_id)
        # Learn
        self._learn(persona, learned, packet_id, memory_errors)
        learned = pre_learned + learned
        return {"status": "completed", "observe": {"pool_version": pool["pool_version"], "members": len(pool["members"]),
                "health": health}, "orient": orient,
                "decide": {k: v for k, v in decision.items() if k not in ("candidate", "member")}
                         | ({"candidate_ref": decision["candidate"]["artifact_ref"]} if "candidate" in decision else {}),
                "act": {"receipts": receipts, "paper_only": True, "discovery": discovery, "publication": publication}, "learn": {"memory_entries": [e["outcome"] for e in learned], "memory_write_errors": memory_errors,
                "tried_families": sorted({e["family"] for e in learned if e.get("family") and e["outcome"] in TRIED_OUTCOMES})}}

    def _discover(self, persona, pool) -> list[dict[str, Any]]:
        """Run discovery for every open demand; one demand's failure never stops the others.

        Ports without a discovery owner (older wiring) skip the step. Missing sources fail
        closed: the reason is recorded on the demand, nothing is guessed.
        """
        ports = self.ports
        if not hasattr(ports, "discover_for_demand"):
            return []
        out: list[dict[str, Any]] = []
        for demand in ports.reconcile_demands(persona, pool["members"]):
            if demand.get("status") != "open":
                continue
            entry: dict[str, Any] = {"demand_id": demand["demand_id"]}
            done = {(a.get("match_id"), a.get("action")) for a in (demand.get("discovery") or {}).get("actions") or []
                    if a.get("status") != "failed"}
            try:
                found = ports.discover_for_demand(persona, demand)
                # An action an owner already took is not executed again; only failures are retried.
                actions = [self._execute_match(persona, demand, m) for m in found["matches"]
                           if (m["match_id"], m["recommended_action"]["type"]) not in done]
                backfill = list(found.get("backfill_datasets") or [])
                if backfill and not found["matches"] and not found.get("partial") and (None, "request_data_backfill") not in done:
                    actions.append(self._execute_match(persona, demand, {
                        "match_id": None, "matched_object_id": None, "matched_object_type": "demand", "score": None,
                        "recommended_action": {"type": "request_data_backfill", "reason": found.get("reason") or "required_data_unavailable"},
                        "missing_data": [{"dataset": d} for d in backfill]}))
                record = {"run_at": self.now(), "matched": len(found["matches"]), "excluded": found["excluded"],
                          "candidates_considered": found["candidates_considered"], "reason": found.get("reason"),
                          "actions": actions}
                if found.get("spec_read"):
                    record["spec_read"] = found["spec_read"]
                if found.get("partial"):
                    record.update(partial=True, **{k: found[k] for k in (
                        "seeds_seen", "seed_pages", "partial_reason", "specs_seen", "spec_pages", "spec_partial_reason")
                        if k in found})
            except Exception as exc:  # noqa: BLE001 - fail closed, record why
                record = {"run_at": self.now(), "matched": 0, "excluded": [], "candidates_considered": 0,
                          "reason": f"discovery_unavailable:{type(exc).__name__}: {str(exc)[:160]}", "actions": []}
            try:
                ports.record_discovery(persona, demand["demand_id"], record)
                entry["recorded"] = True
            except Exception as exc:  # noqa: BLE001
                entry["recorded"] = False
                entry["record_error"] = type(exc).__name__
            entry.update(matched=record["matched"], reason=record["reason"],
                         actions=[{k: a.get(k) for k in ("action", "match_id", "status")} for a in record["actions"]])
            out.append(entry)
        return out

    def _execute_match(self, persona, demand, match) -> dict[str, Any]:
        action = match["recommended_action"]["type"]
        base = {"action": action, "match_id": match["match_id"], "matched_object_id": match["matched_object_id"],
                "score": match["score"]}
        try:
            return {**base, **self.ports.execute_match_action(persona, demand, match)}
        except Exception as exc:  # noqa: BLE001 - the owner refused or was down: record, do not retry blindly
            return {**base, "status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}

    def _publish(self, persona, packet_id) -> dict[str, Any]:
        """Publish the owner's current pool version when it changes what runs; never raises.

        Compared with the newest *confirmed* publication, so an unchanged daily re-weight publishes
        nothing and a failed or unconfirmed publish is retried by the next cycle. A change of live
        membership proceeds only with the verified envelope_publication memo.
        """
        ports = self.ports
        if not hasattr(ports, "publish_pool") or not hasattr(ports, "publication_state"):
            return {"status": "not_attempted", "reason": "publish_port_unconfigured"}
        try:
            from services.control_plane.persona.pool_policy import (
                admission_only, last_published, latest_publication, load_policy, publication_change)

            pool = ports.current_pool(persona)
            history = ports.publication_state(persona)
            latest = latest_publication(history)
            confirmed_dispatch = False
            if latest is not None and latest["status"] == "dispatched":
                # This dispatch already passed the publication gate. Confirm/retry that
                # exact version through the publisher, never request a new consultation.
                pending = {"status": "publication_dispatch_unconfirmed", "reasons": ["publication_dispatch_unconfirmed"],
                           "pool_version": latest["pool_version"],
                           "envelope_artifact_id": latest.get("envelope_artifact_id")}
                try:
                    receipt = ports.publish_pool(persona, latest["pool_version"])
                except Exception as exc:  # owner failure preserves the accepted dispatch
                    return {**pending, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
                if receipt.get("status") != "published":
                    return {**pending, "note": receipt.get("note")}
                # Use the publisher-confirmed pins, without another history/binding read.
                history = {**history, "published": {**history["published"],
                           str(latest["pool_version"]): {**latest, **receipt, "status": "published"}}}
                confirmed_dispatch = True
            confirmed = last_published(history)
            change = publication_change(confirmed, pool, load_policy())
        except Exception as exc:  # noqa: BLE001 - unreadable state is reported, retried next cycle
            return {"status": "failed", "step": "compare", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
        base = {"pool_version": pool["pool_version"], "reasons": change["reasons"]}
        if confirmed is not None and not change["membership_changed"] and hasattr(ports, "supersede_publication_operator_decisions"):
            try:
                retired = ports.supersede_publication_operator_decisions(persona, pool, confirmed)
                if retired:
                    base["superseded_operator_decisions"] = retired
            except Exception as exc:
                # Do not hide a failed retirement behind an unchanged/publication result.
                return {**base, "status": "failed", "step": "supersede_operator_decisions",
                        "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if not change["changed"]:
            return {**base, "status": "unchanged"}
        try:
            if admission_only(change) and pool["pool_version"] <= int(change.get("published_pool_version") or 0):
                # The running envelope lacks member joined_at and nothing else changed. The publisher
                # only moves to a newer pool version, so the owner appends one version with identical
                # members (an envelope republish, not a reweight). Once published, the pins carry
                # joined_at and this branch never fires again for the persona.
                if not hasattr(ports, "republish_pool_version"):
                    return {**base, "status": "not_attempted", "reason": "republish_port_unconfigured"}
                pool = ports.republish_pool_version(persona, pool, change["reasons"])
                base = {**base, "pool_version": pool["pool_version"], "republished_version": pool["pool_version"]}
            if change["membership_changed"]:
                consult = ports.envelope_publication_consultation(persona, pool, packet_id)
                if consult["state"] == "operator" and hasattr(ports, "publication_operator_decision"):
                    held = self._publication_operator_gate(persona, pool, consult)
                    if held is not None:
                        return {**base, **held}
                    base.update(operator_decision_id=ports.publication_operator_decision_id(consult),
                                consultation_request_id=consult.get("request_id"))
                elif consult["state"] != "verified":
                    reason = consult.get("reason") or "consultation_" + consult["state"]
                    return {**base, "status": "consultation_" + consult["state"], "reason": reason,
                            "request_id": consult.get("request_id"), "decision_id": consult.get("decision_id")}
                else:
                    base["consultation_memo_id"] = consult["memo_id"]
            if confirmed_dispatch:
                # One owner binding snapshot per cycle: the confirmation has used
                # this tick's budget. Publish the changed version on the next tick.
                return {**base, "status": "published", "pool_version": latest["pool_version"],
                        "next_pool_version": pool["pool_version"], "publication_pending": True,
                        "envelope_artifact_id": latest.get("envelope_artifact_id")}
            receipt = ports.publish_pool(persona, pool["pool_version"])
        except Exception as exc:  # noqa: BLE001 - the previous envelope keeps running; the next cycle retries
            return {**base, "status": "failed", "step": "publish", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
        status = receipt.get("status")
        return {**base, "status": "published" if status == "published" else "unconfirmed",
                "envelope_artifact_id": receipt.get("envelope_artifact_id"),
                "deployment_plan_id": receipt.get("deployment_plan_id"), "note": receipt.get("note")}

    OPERATOR_APPROVED = ("approved", "executed")
    OPERATOR_CLOSED = ("rejected", "canceled", "cancelled", "superseded")

    def _publication_operator_gate(self, persona, pool, consult) -> dict[str, Any] | None:
        """An escalated envelope publication waits for, and then follows, the operator's decision.

        Returns None only when the operator approved this exact consultation (membership + pool version):
        the caller then publishes. Otherwise the cycle result: the decision is queued once through the
        governance queue trial admissions use and the publication stays held (``consultation_operator``),
        or the operator rejected it (``operator_rejected``) and the previous envelope keeps running.
        """
        ports = self.ports
        held = {"status": "consultation_operator", "reason": consult.get("reason") or "consultation_operator",
                "request_id": consult.get("request_id"), "decision_id": consult.get("decision_id")}
        try:
            decision = ports.publication_operator_decision(persona, consult)
        except Exception as exc:  # noqa: BLE001 - an unreadable decision is never an approval
            return {**held, "operator_decision": {"status": "read_failed",
                                                  "error": f"{type(exc).__name__}: {str(exc)[:160]}"}}
        if decision is None:
            try:
                queued = ports.queue_publication_operator_decision(persona, pool, consult)
            except Exception as exc:  # noqa: BLE001 - still held; the next cycle queues again
                return {**held, "operator_decision": {"decision_id": ports.publication_operator_decision_id(consult),
                                                      "status": "queue_failed",
                                                      "error": f"{type(exc).__name__}: {str(exc)[:160]}"}}
            return {**held, "operator_decision": queued}
        state = decision["decision_state"]
        if state in self.OPERATOR_APPROVED:
            return None
        if state in self.OPERATOR_CLOSED:
            return {**held, "status": "operator_rejected", "reason": decision.get("note") or "operator_" + state,
                    "operator_decision": {"decision_id": decision["decision_id"], "status": state,
                                          "actor_id": decision.get("actor_id")}}
        return {**held, "operator_decision": {"decision_id": decision["decision_id"], "status": "awaiting_operator",
                                              "decision_state": state}}

    def _learn(self, persona, entries, packet_id, errors):
        for entry in entries:
            try:
                self.ports.write_memory(persona, entry | {"packet_id": packet_id})
            except Exception as exc:  # noqa: BLE001 - the action already happened; record, do not hide
                errors.append(f"{entry['outcome']}:{type(exc).__name__}")

    def _consult_gate(self, persona, pool, cand, packet_id) -> dict[str, Any]:
        """Trial admission goes through the risk persona consultation (design D6 / 6.4).

        Asynchronous: one cycle opens the panel, a later cycle verifies the published memo.
        Returns ``memo_id`` only when the memo verified; otherwise the receipts and memory
        entries (pending / operator decision / unavailable) and no admission. A pending
        consultation is never recorded as a refusal or a tried family.
        """
        ports, route = self.ports, "consultation:trial_admission"
        out: dict[str, Any] = {"receipts": [], "learned": []}
        base = {"route": route, "action": "admit", "artifact_ref": cand["artifact_ref"]}
        try:
            if not hasattr(ports, "trial_admission_consultation"):
                raise RuntimeError("consultation_owner_unconfigured")
            consult = ports.trial_admission_consultation(persona, pool, cand, packet_id)
        except Exception as exc:  # noqa: BLE001 - missing consultation data fails closed with the reason recorded
            out["receipts"].append({**base, "status": "not_attempted",
                                    "reason": f"consultation_unavailable:{type(exc).__name__}: {str(exc)[:120]}"})
            return out
        state = consult["state"]
        ids = {k: consult.get(k) for k in ("decision_id", "request_id")}
        if state in ("opened", "pending"):
            out["receipts"].append({**base, **ids, "status": "consultation_" + state})
            out["learned"].append({"outcome": "admission_pending", "family": cand.get("family"),
                                   "artifact_ref": cand["artifact_ref"],
                                   "detail": f"risk consultation {state}: {consult.get('request_id')}"})
        elif state == "operator":
            receipt = {**base, **ids, "status": "operator_decision_required", "reason": consult["reason"]}
            try:
                receipt["operator_decision"] = ports.queue_operator_decision(persona, pool, cand, consult)
            except Exception as exc:  # noqa: BLE001 - still never auto-admitted
                receipt["queue_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            out["receipts"].append(receipt)
            out["learned"].append({"outcome": "admission_operator_decision", "family": cand.get("family"),
                                   "artifact_ref": cand["artifact_ref"], "detail": consult["reason"]})
        elif state == "verified":
            out["memo_id"], out["pool_version"] = consult["memo_id"], consult["pool_version"]
        else:
            out["receipts"].append({**base, **ids, "status": "not_attempted", "reason": "consultation_state_unknown:" + str(state)})
        return out

    def _act(self, persona, pool, observe, health, decision, tried, packet_id):
        ports, kind = self.ports, decision["kind"]
        receipts: list[dict[str, Any]] = []
        learned: list[dict[str, Any]] = []
        if kind == "open_demand":
            body = {"persona_id": persona["persona_id"], "mandate": persona.get("mandate") or persona.get("name") or persona["persona_id"],
                    "missing_datasets": ports.missing_datasets(persona, pool), "tried_families": tried,
                    "short_measure": decision["short_measure"], "pool_version": pool["pool_version"],
                    "evidence_ref": f"ooda://{packet_id}"}
            result = ports.open_demand(persona, body)
            receipts.append({"route": "research:persona-demands", "demand_id": result["demand_id"], "created": result.get("created", True)})
        elif kind == "admit_trial":
            cand = decision["candidate"]
            gate = self._consult_gate(persona, pool, cand, packet_id)
            receipts.extend(gate["receipts"])
            learned.extend(gate["learned"])
            if not gate.get("memo_id"):
                return receipts, learned
            try:
                dataset = ports.admission_dataset(persona, cand)
            except Exception as exc:  # noqa: BLE001 - missing data fails closed, never synthetic
                receipts.append({"route": "persona:strategy-pool/decisions", "action": "admit", "status": "not_attempted",
                                 "reason": "governed_dataset_unavailable:" + type(exc).__name__
                                 + (": " + str(exc) if str(exc).startswith("feature dataset coverage missing") else "")})
                return receipts, learned
            try:
                cand_book = ports.candidate_book(persona, cand, dataset)
            except Exception as exc:  # noqa: BLE001 - no replay, no admission; never fabricate returns
                receipts.append({"route": "persona:strategy-pool/decisions", "action": "admit", "status": "not_attempted",
                                 "reason": "candidate_book_unavailable:" + type(exc).__name__})
                return receipts, learned
            result = ports.decide_pool(persona, {
                "expected_version": pool["pool_version"], "execution_context": "paper", "action": "admit",
                "initiated_by": "persona", "consultation_memo_id": gate["memo_id"],
                "consultation_pool_version": gate["pool_version"],
                "candidate": {"artifact_ref": cand["artifact_ref"], "checksum": cand["checksum"],
                              **({"demand_ref": cand["demand_ref"]} if cand.get("demand_ref") else {})},
                "books": {**observe.get("books", {}), cand["artifact_ref"]: cand_book},
                "observations": observe.get("observations", []), "dataset": dataset})
            status = (result.get("decision") or {}).get("status")
            receipts.append({"route": "persona:strategy-pool/decisions", "action": "admit", "status": status,
                             "consultation_memo_id": gate["memo_id"], "pool_version": result.get("pool_version"), "reasons": (result.get("decision") or {}).get("reasons", [])})
            learned.append({"outcome": "admitted" if status == "applied" else "admission_refused",
                            "family": cand.get("family"), "artifact_ref": cand["artifact_ref"],
                            "detail": "; ".join((result.get("decision") or {}).get("reasons", []))})
            if status == "applied":
                # Full-budget admission replaces the weakest member: learn every removal by diff.
                after_live = {m["artifact_ref"] for m in result.get("members") or [] if m["stage"] != "removed"}
                gone = {m["artifact_ref"] for m in pool["members"] if m["stage"] != "removed"} - after_live
                replaced = (result.get("decision") or {}).get("replaced_artifact_ref")
                for ref in sorted(gone | ({replaced} if replaced else set())):
                    learned.append({"outcome": "removed", "family": ports.family(persona, ref), "artifact_ref": ref,
                                    "detail": "replaced_by_stronger_trial:" + cand["artifact_ref"]})
        elif kind in ("reweight", "remove"):
            result = ports.decide_pool(persona, {
                "expected_version": pool["pool_version"], "execution_context": "paper", "action": "reweight",
                "books": observe.get("books", {}), "observations": observe.get("observations", [])})
            after = result.get("members") or []
            before_live = {m["artifact_ref"] for m in pool["members"] if m["stage"] != "removed"}
            removed = sorted(before_live - {m["artifact_ref"] for m in after if m["stage"] != "removed"}) if after else []
            receipts.append({"route": "persona:strategy-pool/decisions", "action": "reweight",
                             "status": (result.get("decision") or {}).get("status"), "removed": removed,
                             "pool_version": result.get("pool_version")})
            for ref in removed:
                learned.append({"outcome": "removed", "family": ports.family(persona, ref), "artifact_ref": ref,
                                "detail": "own_high_water_drawdown"})
            if removed:
                decision["kind"] = "remove"
        elif kind == "propose_mutate":
            member = decision["member"]
            receipts.append({"route": "evolution:proposals", **ports.propose_evolution(persona, pool, member)})
        elif kind == "propose_widen":
            receipts.append({"route": "none", "status": "staged_no_universe_owner",
                             "note": "permitted-universe owner absent; widening is proposed, not applied"})
            learned.append({"outcome": "widen_proposed", "family": None, "artifact_ref": None,
                            "detail": f"tried {len(tried)} families while effective bets short"})
        else:
            receipts.append({"route": "none", "status": "no_action", "reason": decision["reason"]})
        return receipts, learned


def apply_to_packet(packet: dict[str, Any], result: Mapping[str, Any]) -> None:
    """Fill the persona's daily OODA record with the pool cycle's real content."""
    packet["pool_autonomy"] = dict(result)
    publication = (result.get("act") or {}).get("publication") or {}
    if publication.get("status") in ("failed", "unconfirmed", "publication_dispatch_unconfirmed", "operator_rejected"):
        packet["audit_refs"].append(
            f"pool-publication:{publication['status']}:{publication.get('reason') or publication.get('note')}"[:300])
    if result.get("status") != "completed":
        packet["audit_refs"].append(f"pool-autonomy:{result.get('status')}:{result.get('reason')}"[:300])
        return
    ref = f"{packet['packet_id']}#pool_autonomy"
    packet["observe"]["telemetry_refs"].append(ref + "/observe")
    packet["orient"]["evidence_bundle_refs"].append(ref + "/orient")
    packet["decide"]["decision_rationale_ref"] = ref + "/decide"
    packet["decide"]["sponsor_persona_id"] = result["persona_id"]
    packet["act"]["command_receipt_refs"] = list(packet["act"].get("command_receipt_refs", [])) + [ref + "/act"]
    packet["act"]["live_capital_side_effects"] = False
    packet["learn"]["telemetry_refs"].append(ref + "/learn")


def candidate_virtual_book(*, dataset, artifact, checksum, policy) -> dict[str, Any]:
    """Governed replay book for a not-yet-admitted candidate (telemetry has none before admission).

    Reuses admission's replay (same registry interpreter, next-open fills of close-decided
    exposure and Taiwan-cost netting) over the untouched window of the governed daily bars.
    Nothing is invented: unavailable or misaligned data raises.
    """
    from services.execution.lean_runtime.member_virtual_book import FILL_CONVENTION, NET_BASIS
    from services.research.pool_admission import _replay
    from services.research.vectorbt.adapter.vectorbt_adapter import GovernedVectorbtInputAdapter

    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    if prepared.data_frequency not in ("daily", "1d"):
        raise ValueError("backtest_requires_daily_bars")
    calendars = [tuple(prepared.dates_by_instrument[s]) for s in prepared.instruments]
    if len(set(calendars)) != 1:
        raise ValueError("backtest_windows_not_aligned")
    dates = list(calendars[0][int(len(calendars[0]) * policy.in_sample_fraction):])
    returns = _replay(prepared, artifact, dates)
    return {"checksum": checksum, "source_ref": f"governed-replay:{prepared.dataset_id}", "basis": NET_BASIS,
            "fill_convention": FILL_CONVENTION, "dates": dates[1:], "returns": returns, "since_inception": True, "book_kind": "governed_replay"}


def governed_dataset_loader(env: Mapping[str, str] | None = None) -> Callable[[Mapping, Mapping], dict]:
    """Admission dataset from the same governed evaluation authority research uses.

    Fails closed (raises) when the dataset/policy authority is not configured or
    rejected; never synthesises data.
    """
    source = os.environ if env is None else env

    def load(persona: Mapping, candidate: Mapping) -> dict:
        dataset_path = source.get("PERSONA_POOL_CANONICAL_DATASET_PATH") or source.get("TRAINING_SESSION_CANONICAL_DATASET_PATH")
        policy_path = source.get("PERSONA_POOL_THRESHOLD_POLICY_PATH") or source.get("TRAINING_SESSION_THRESHOLD_POLICY_PATH")
        if not dataset_path or not policy_path:
            raise RuntimeError("governed_dataset_loader_unconfigured")
        import sys
        from pathlib import Path
        training_dir = str(Path(__file__).resolve().parents[1] / "training-session")
        sys.path.insert(0, training_dir)
        try:
            import evaluation_authority as authority  # type: ignore
            snapshot = authority.load_evaluation_authority(
                dataset_path, policy_path, trusted_now=datetime.now(timezone.utc), strategy_id=str(candidate["artifact_ref"]))
            return dict(snapshot.vectorbt_dataset)
        finally:
            if training_dir in sys.path:
                sys.path.remove(training_dir)
    return load


def governed_discovery_loader(*, seeds: Callable[[Mapping], list], specs: Callable[[Mapping], list] | None = None,
                              route_policy: Callable[[Mapping], Mapping | None],
                              capability_snapshot: Callable[[Mapping], Mapping | None]) -> Callable[[Mapping], dict]:
    """Discovery inputs from the existing owners (seed store, registry specs, persona route policy / capability).

    Each source is an owner read; one that raises makes the demand's discovery fail closed with that reason.
    """
    def load(persona: Mapping) -> dict:
        return {"seeds": seeds(persona), "specs": specs(persona) if specs else None, "route_policy": route_policy(persona) or {},
                "capability_snapshot": capability_snapshot(persona) or {}}
    return load


class StrategySpecRead(list):
    """Registry StrategySpecs read for discovery plus how complete and how large the read was."""

    def __init__(self, rows: Any = (), *, resumed: bool = False, limits: Mapping[str, Any] | None = None):
        super().__init__(rows)
        self.pages, self.bytes_read, self.seconds = 0, 0, 0.0
        self.partial, self.stop_reason, self.next_token = False, None, None
        self.resumed, self.limits = resumed, dict(limits or {})

    def stop(self, reason: str, next_token: str | None) -> None:
        self.partial, self.stop_reason, self.next_token = True, reason, next_token

    def summary(self) -> dict[str, Any]:
        return {"specs_seen": len(self), "pages": self.pages, "bytes": self.bytes_read, "seconds": self.seconds,
                "partial": self.partial, "stop_reason": self.stop_reason, "resumed": self.resumed,
                "max_pages": self.limits.get("max_pages"), "page_size": self.limits.get("page_size"),
                "budget_seconds": self.limits.get("budget_seconds")}


class HttpPoolCyclePorts(PoolPublishPort):
    """Owner HTTP ports. ``transport(method, service, path, tenant, body)`` is the only I/O."""

    SERVICE_URL_ENV = {"persona": ("PERSONA_API_URL", "http://persona:8002"),
                       "research": ("RESEARCH_API_URL", "http://research-orchestrator:8100"),
                       "memory": ("MEMORY_API_URL", "http://memory:8086"),
                       "evolution": ("EVOLUTION_API_URL", "http://evolution:8093"),
                       "registry": ("REGISTRY_URL", "http://registry:8087"),
                       "bff": ("PANTHEON_BFF_URL", "http://operator-bff:8001")}

    # Owner-specific credentials already configured for the persona service; the
    # persona owner itself is called in-process through ``pool_store`` (no self-HTTP).
    TOKEN_VARIABLE = {"research": "PERSONA_RESEARCH_SERVICE_TOKEN", "memory": "PANTHEON_MEMORY_AUTH_TOKEN",
                      "evolution": "EVOLUTION_AUTH_TOKEN", "registry": "PERSONA_REGISTRY_SERVICE_TOKEN",
                      "bff": "PERSONA_POOL_PUBLISH_BFF_TOKEN"}

    def __init__(self, transport: Callable[..., Any] | None = None, *, pool_store: Any = None,
                 dataset_loader: Callable[[Mapping, Mapping], dict] | None = None, env: Mapping[str, str] | None = None,
                 discovery_loader: Callable[[Mapping], dict] | None = None,
                 consultation_factory: Callable[..., Any] | None = None,
                 spec_clock: Callable[[], float] | None = None):
        self.consultation_factory = consultation_factory or self._consultation
        self.env = os.environ if env is None else env
        self.transport = transport or self._urllib
        self.pool_store = pool_store
        self.dataset_loader = dataset_loader
        self.discovery_loader = discovery_loader
        self._entries: dict[tuple[str, str], Mapping] = {}
        self.spec_clock = spec_clock or time.monotonic
        self._spec_reads: dict[str, tuple[float, StrategySpecRead]] = {}
        self._spec_cursor: dict[str, str] = {}

    def _urllib(self, method, service, path, tenant, body=None):
        from services.service_token_file import configured_service_token
        name, default = self.SERVICE_URL_ENV[service]
        variable = self.TOKEN_VARIABLE[service]
        if service == "memory" and method == "GET":
            variable = "PANTHEON_MEMORY_SERVICE_TOKEN"  # read-only persona_session principal; writes use the write principal
        token = configured_service_token(variable, self.env)
        if not token:  # fail closed: never send an empty bearer
            raise RuntimeError(f"owner_credential_unconfigured:{service}")
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant,
                   "Content-Type": "application/json"}
        if service == "evolution" and method == "POST" and path.endswith("/supersede"):
            publication_token = configured_service_token("PERSONA_POOL_PUBLISH_BFF_TOKEN", self.env)
            if not publication_token:
                raise RuntimeError("owner_credential_unconfigured:publication_authority")
            headers["X-Pool-Publication-Authorization"] = f"Bearer {publication_token}"
        request = Request(self.env.get(name, default).rstrip("/") + path, method=method,
                          data=None if body is None else json.dumps(body).encode(), headers=headers)
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    def _call(self, method, service, path, persona, body=None):
        return self.transport(method, service, path, persona["tenant_id"], body)

    def current_pool(self, persona):
        return self.pool_store.current(persona["persona_id"], persona["tenant_id"])

    def reconcile_demands(self, persona, members):
        out = self._call("POST", "research", "/api/research/persona-demands/reconcile", persona,
                         {"persona_id": persona["persona_id"], "members": members})
        return out["demands"]

    def open_demand(self, persona, body):
        return self._call("POST", "research", "/api/research/persona-demands", persona, body)

    def record_discovery(self, persona, demand_id, record):
        return self._call("POST", "research", f"/api/research/persona-demands/{quote(demand_id, safe='')}/actions",
                          persona, record)

    def discover_for_demand(self, persona, demand):
        """The existing discovery scorer, filtered by the demand. Unconfigured sources fail closed."""
        if self.discovery_loader is None:
            raise RuntimeError("discovery_sources_unconfigured")
        import importlib
        module = importlib.import_module("services.control_plane.persona.persona_strategy_discovery")
        sources = self.discovery_loader(persona)
        # Negative memory is the seed store's own verdict (carried on each seed); the persona's
        # own failed families arrive through the demand's tried_families.
        seeds = sources.get("seeds") or []
        specs = self.read_strategy_specs(persona) if sources.get("specs") is None else sources["specs"]
        found = module.discover_for_demand(
            persona, demand, route_policy=sources.get("route_policy"), capability_snapshot=sources.get("capability_snapshot"),
            strategy_seeds=seeds, strategy_specs=specs,
            created_at=self.now_iso(), discovery_session_id="psd-demand-" + demand["demand_id"])
        if isinstance(specs, StrategySpecRead):
            found = {**found, "spec_read": specs.summary()}
        if getattr(seeds, "partial", False):
            # The seed read stopped at its cap: say so, with how much was seen, and never report "no match".
            found = {**found, "partial": True, "seeds_seen": len(seeds), "seed_pages": getattr(seeds, "pages", None),
                     "partial_reason": getattr(seeds, "stop_reason", None)}
            if not found["matches"]:
                found["reason"] = f"partial_seed_read:{len(seeds)}_seeds_seen_no_match_yet"
        if getattr(specs, "partial", False):
            # The registry spec read stopped at its page cap, time budget or a failed later page: the
            # candidates beyond it were not seen, so "no match" is never claimed.
            found = {**found, "partial": True, "specs_seen": len(specs), "spec_pages": specs.pages,
                     "spec_partial_reason": specs.stop_reason}
            if not found["matches"]:
                found["reason"] = (found["reason"] if str(found.get("reason") or "").startswith("partial_seed_read")
                                   else f"partial_spec_read:{len(specs)}_specs_seen_no_match_yet")
        return found

    @staticmethod
    def now_iso():
        return _now()

    def read_strategy_specs(self, persona) -> "StrategySpecRead":
        """StrategySpec candidates from the registry owner, one bounded page at a time.

        Reads ``view=discovery_candidates`` (retired/rejected excluded by the owner, five entry
        fields only) for the persona's tenant with the same registry credential and X-Tenant-Id.
        Stops at ``max_pages`` or ``budget_seconds`` (``partial`` with ``stop_reason``); a capped
        read resumes from where it stopped on the next read for that tenant, so a large registry
        is covered across cycles. A failed first page raises (unavailable); a failed later page
        keeps what was read as partial. A read is reused for ``reuse_seconds`` by every demand
        and persona of the same tenant, so one cycle never walks the registry once per demand.
        Each spec keeps only the fields the discovery scorer reads (``SCORER_SPEC_KEYS``).
        """
        tenant = persona["tenant_id"]
        cfg = self.spec_read_limits()
        now = self.spec_clock()
        cached = self._spec_reads.get(tenant)
        if cached is not None and now - cached[0] < cfg["reuse_seconds"]:
            return cached[1]
        start_token = self._spec_cursor.pop(tenant, None)
        read = StrategySpecRead(resumed=start_token is not None, limits=cfg)
        token, started = start_token, now
        while True:
            if read.pages >= cfg["max_pages"]:
                read.stop("page_cap", token)
                break
            if read.pages and self.spec_clock() - started >= cfg["budget_seconds"]:
                read.stop("time_budget", token)
                break
            query = {"view": "discovery_candidates", "tenant_id": tenant, "page_size": cfg["page_size"]}
            if token:
                query["page_token"] = token
            try:
                page = self._call("GET", "registry", "/api/registry/strategy-specs?" + urlencode(query), persona)
                items = page["items"]
                if not isinstance(items, list):
                    raise TypeError("discovery_candidates items is not a list")
            except Exception as exc:  # noqa: BLE001 - an unreadable owner is never an empty registry
                if not read.pages:
                    raise RuntimeError(f"strategy_spec_read_unavailable:pages=0:specs=0:{type(exc).__name__}: "
                                       f"{str(exc)[:120]}") from exc
                read.stop(f"page_failed:{type(exc).__name__}", None)
                break
            read.pages += 1
            read.bytes_read += len(json.dumps(page, default=str))
            read.extend(spec for spec in (self._spec_from_view(view) for view in items) if spec is not None)
            token = page.get("next_page_token")
            if not token:
                if start_token is not None:
                    read.stop("resumed_tail", None)  # saw only the tail; the next read starts from the beginning
                break
        if read.next_token:
            self._spec_cursor[tenant] = read.next_token
        read.seconds = round(self.spec_clock() - started, 3)
        self._spec_reads[tenant] = (now, read)
        return read

    # Only what ``persona_strategy_discovery`` reads from a StrategySpec (``_candidate_from_strategy_spec``
    # and the demand pre-filter), plus the registry id and lineage the cycle has always carried.
    SCORER_SPEC_KEYS = frozenset({
        "strategy_id", "id", "spec_version_id", "title", "name", "hypothesis", "strategy_family", "source_kind",
        "market_scope", "execution_profile", "governance", "provenance", "citation_bundle", "evidence_refs",
        "derived_from_source_refs", "source_refs", "data_dependencies", "lifecycle_state", "status", "allowed_use",
        "metadata", "mechanism", "mechanism_status", "mechanism_support", "negative_memory_match",
        "strategy_spec_registry_id", "lineage"})

    def spec_read_limits(self) -> dict[str, Any]:
        env = self.env
        return {"page_size": max(1, min(50, int(env.get("PERSONA_DISCOVERY_SPEC_PAGE_SIZE") or 50))),
                "max_pages": max(1, int(env.get("PERSONA_DISCOVERY_SPEC_MAX_PAGES") or 40)),
                "budget_seconds": float(env.get("PERSONA_DISCOVERY_SPEC_BUDGET_SECONDS") or 30),
                "reuse_seconds": float(env.get("PERSONA_DISCOVERY_SPEC_REUSE_SECONDS") or 600)}

    @classmethod
    def _spec_from_view(cls, view) -> dict | None:
        entry = view.get("entry") if isinstance(view, Mapping) else None
        if not isinstance(entry, Mapping) or str(entry.get("artifact_state") or "").lower() in {"retired", "rejected"}:
            return None  # the owner already excludes these; kept so a stale owner never leaks one
        metadata = dict(entry.get("metadata") or {})
        stored = metadata.get("strategy_spec")  # the registry stores the canonical StrategySpec here
        spec = dict(stored) if isinstance(stored, Mapping) else {}
        for key in ("mechanism", "mechanism_status", "mechanism_support", "negative_memory_match"):
            if key in metadata:
                spec.setdefault(key, metadata[key])
        spec.update({"strategy_id": spec.get("strategy_id") or entry.get("strategy_id"),
                     "strategy_spec_registry_id": entry.get("registry_id"),
                     "lifecycle_state": entry.get("artifact_state"),
                     "metadata": {**(spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}),
                                  **{k: v for k, v in metadata.items() if k != "strategy_spec"}},
                     "lineage": entry.get("lineage") if isinstance(entry.get("lineage"), Mapping) else {}})
        return {k: v for k, v in spec.items() if k in cls.SCORER_SPEC_KEYS}

    # Each supported action is admitted by the research owner as a durable ticket (its request queue).
    # The persona cycle only asks: seed promotion stays with the seed reviewer, rapid evaluation with
    # the governed training session and data backfill with the data owners; the ticket is their gate.
    ACTION_REQUEST = {
        "create_research_ticket": ("created", "Research"),
        "promote_seed_candidate": ("review_requested", "Seed promotion review (reviewer decision required)"),
        "run_rapid_eval": ("eval_requested", "Rapid evaluation request (governed dataset and session required)"),
        "request_data_backfill": ("backfill_requested", "Data backfill request"),
    }

    @staticmethod
    def _is_not_found(exc):
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        return code == 404 or isinstance(exc, LookupError)

    def execute_match_action(self, persona, demand, match):
        """Execute a recommended action through its existing owner (paper/research only)."""
        action = match["recommended_action"]["type"]
        if action not in self.ACTION_REQUEST:
            return {"owner": "none", "status": "refused", "reason": "unsupported_action:" + action}
        done_status, label = self.ACTION_REQUEST[action]
        key = f"{demand['demand_id']}:{match.get('match_id')}:{action}"
        ticket_id = "rt-demand-" + hashlib.sha256(key.encode()).hexdigest()[:16]
        try:  # the owner overwrites on a repeated id: never post an existing ticket again
            existing = self._call("GET", "research", "/api/research/tickets/" + ticket_id, persona)
        except Exception as exc:  # noqa: BLE001 - only a confirmed absence (404) may create; the owner overwrites a repeated id
            if not self._is_not_found(exc):
                raise
            existing = None
        if existing:
            return {"owner": "research:tickets", "status": "already_exists", "ticket_id": ticket_id}
        datasets = sorted(d["dataset"] for d in match.get("missing_data", []))
        subject = f"{match.get('matched_object_type')} {match.get('matched_object_id')}" if match.get("matched_object_id") else "demand datasets " + ",".join(datasets)
        ticket = self._call("POST", "research", "/api/research/tickets", persona, {
            "ticket_id": ticket_id, "owner": persona["persona_id"], "actor_id": "persona-pool-cycle", "priority": "medium",
            "title": f"{label}: demand {demand['demand_id']}: {subject}",
            "description": (f"Persona {persona['persona_id']} is short on {demand['short_measure']}. Action {action}"
                            f" (score {match.get('score')}): {match['recommended_action'].get('reason')} "
                            f"Datasets: {datasets or 'n/a'}. Demand evidence: {demand.get('evidence_ref')}.")})
        receipt = {"owner": "research:tickets", "status": done_status, "ticket_id": (ticket or {}).get("ticket_id", ticket_id)}
        if datasets:
            receipt["datasets"] = datasets
        return receipt

    def candidates(self, persona, pool):
        """Gate-passed StrategyArtifact candidates resolved through owner evidence.

        alpha-replication entries describe a reviewed StrategySpec (its id/checksum),
        not the executable artifact. Only a completed, un-failed research entry is
        followed to the registry's approved execution bundle whose lineage names that
        spec and whose own gate proof resolves; anything else is skipped so an unusable
        entry can never starve valid supply. Demand-originated candidates are scoped to
        the exact tenant and persona that opened the demand.
        """
        from services.evolution.approved_baselines import resolve_member_baseline
        rows = self._call("GET", "research", "/api/research-orchestrator/alpha-replication/entries", persona)
        in_pool = {m["artifact_ref"] for m in (pool or {}).get("members", []) if m.get("stage") != "removed"}
        if callable(getattr(self.pool_store, "versions", None)):
            for ver in self.pool_store.versions(persona["persona_id"], persona["tenant_id"]):
                dec = ver.get("decision") or {}
                cand_ref = (dec.get("candidate") or {}).get("artifact_ref")
                if dec.get("action") in ("admit", "evolution_trial") and cand_ref:
                    in_pool.add(cand_ref)
        out, seen = [], set()
        for row in rows or []:
            if row.get("tenant_id") and row.get("tenant_id") != persona["tenant_id"]:
                continue
            row_persona = row.get("persona_id")
            if row_persona and row_persona != persona["persona_id"]:
                continue
            spec_id, family = row.get("strategy_spec_id"), row.get("strategy_id")
            if (row.get("admission_status") or row.get("status")) != "admitted" or row.get("queue_status") != "completed" \
                    or row.get("last_error") or not spec_id or not family:
                continue
            try:
                versions = self._call("GET", "registry", "/api/registry/strategies/" + quote(str(family), safe="") + "/entries", persona)
            except Exception:  # noqa: BLE001 - unreadable owner evidence is not a candidate
                continue
            for view in versions or []:
                # The registry owner returns RegistryEntryView rows: {entry, deployment_stage}.
                entry = view.get("entry") if isinstance(view, dict) else None
                if not isinstance(entry, dict):
                    continue
                if entry.get("owner_tenant") and entry.get("owner_tenant") != persona["tenant_id"]:
                    continue
                meta = entry.get("metadata") or {}
                summary = entry.get("evaluation_summary") or {}
                entry_persona = meta.get("persona_id") or summary.get("persona_id")
                if entry_persona and entry_persona != persona["persona_id"]:
                    continue
                demand_ref = row.get("demand_ref") or meta.get("demand_ref") or summary.get("demand_ref")
                if row.get("demand_ref") and meta.get("demand_ref") and row.get("demand_ref") != meta.get("demand_ref"):
                    continue
                if demand_ref and (row_persona or entry_persona) != persona["persona_id"]:
                    continue
                ref = entry.get("registry_id")
                artifact = meta.get("strategy_artifact")
                if (not ref or ref in in_pool or ref in seen or entry.get("artifact_type") != "execution_bundle"
                        or (entry.get("lineage") or {}).get("source_strategy_spec_id") != spec_id or not artifact):
                    continue
                baseline = resolve_member_baseline(
                    tenant_id=persona["tenant_id"], persona_id=persona["persona_id"], artifact_ref=ref,
                    checksum=entry.get("checksum") or "", entry=entry)
                if baseline.get("status") != "approved":
                    continue
                self._entries[(persona["tenant_id"], ref)] = entry
                seen.add(ref)
                out.append({"artifact_ref": ref, "checksum": entry["checksum"],
                            "family": artifact.get("strategy_id") or family, "demand_ref": demand_ref})
        return out

    def _entry(self, persona, ref):
        key = (persona["tenant_id"], ref)
        if key not in self._entries:
            payload = self._call("GET", "registry", "/api/registry/entries/" + quote(ref, safe=""), persona)
            self._entries[key] = payload.get("entry") or {}
        return self._entries[key]

    def family(self, persona, ref):
        try:
            return (self._entry(persona, ref).get("metadata") or {}).get("strategy_artifact", {}).get("strategy_id")
        except Exception:  # noqa: BLE001 - an unreadable family only weakens exclusion
            return None

    def missing_datasets(self, persona, pool):
        have = set()
        for m in pool["members"]:
            if m["stage"] != "removed":
                have |= set(((self._entry(persona, m["artifact_ref"]).get("metadata") or {}).get("data_sources")) or [])
        return sorted(set(persona.get("required_data_sources") or []) - have)

    def admission_dataset(self, persona, candidate):
        if self.dataset_loader is None:
            raise RuntimeError("governed_dataset_loader_unconfigured")
        # The loader needs the rule to load every governed dataset it references.
        artifact = (self._entry(persona, candidate["artifact_ref"]).get("metadata") or {}).get("strategy_artifact")
        if artifact and "strategy_artifact" not in candidate:
            candidate = {**candidate, "strategy_artifact": artifact}
        return self.dataset_loader(persona, candidate)

    def candidate_book(self, persona, candidate, dataset):
        """Candidate's governed replay book; the telemetry owner has none before admission."""
        from services.control_plane.persona.pool_policy import load_policy
        artifact = (self._entry(persona, candidate["artifact_ref"]).get("metadata") or {}).get("strategy_artifact")
        if not artifact:
            raise RuntimeError("candidate_strategy_artifact_missing")
        return candidate_virtual_book(dataset=dataset, artifact=artifact, checksum=candidate["checksum"], policy=load_policy())

    @staticmethod
    def _consultation(tenant: str, service_actor: str | None = None) -> Any:
        from services.consultation.client import ConsultationServiceClient
        return ConsultationServiceClient(tenant_id=tenant, **({"service_actor": service_actor} if service_actor else {}))

    CONSULT_FAILED_STATES = ("canceled", "cancelled", "rejected", "failed", "expired")

    def trial_admission_consultation(self, persona, pool, cand, packet_id):
        """Open (once) or read the risk panel for this exact candidate and pool version.

        The consultation owner is called as ``persona:<id>`` (service caller carrying the persona id);
        the decision id binds artifact_ref + checksum + pool version, so a repeat cycle finds the same
        request instead of opening another. Verification is the shared published-memo check.
        """
        from services.persona.consult_triggers import (
            candidate_digest, pool_membership_signature, trial_admission_requirement, verify_published_memo)
        pid, tenant = persona["persona_id"], persona["tenant_id"]
        digest = candidate_digest(cand["artifact_ref"], cand["checksum"])
        reader = self.consultation_factory(tenant)
        signature = pool_membership_signature(pool["members"])
        # The daily risk reweight appends a pool version every cycle, so the consultation is the newest one for
        # this exact candidate whose pool version still has today's live membership (weights are not membership).
        found = []
        for r in reader.list_requests(target_type="trial_admission", tenant_id=tenant) or []:
            gated = (r.get("metadata") or {}).get("panel", {}).get("gated_decision", {})
            if r.get("from_persona_id") != pid or gated.get("candidate_digest") != digest:
                continue
            version = int(gated.get("target_version") or 0)
            if 1 <= version <= pool["pool_version"] and pool_membership_signature(
                    self.pool_store.version(pid, tenant, version)["members"]) == signature:
                found.append((version, r))
        if not found:
            requirement = trial_admission_requirement(tenant, pid, cand["artifact_ref"], cand["checksum"], pool["pool_version"])
            created = self.consultation_factory(tenant, f"persona:{pid}").open_persona_panel(
                requirement, persona_id=pid, trace_id=f"ooda://{packet_id}", tenant_id=tenant,
                task=f"Risk review of paper trial admission of {cand['artifact_ref']} (pool v{pool['pool_version']})",
                evidence_refs=[f"ooda://{packet_id}", cand["artifact_ref"]],
                request_id="consult-" + requirement.decision_id)
            return {"state": "opened", "request_id": created["request_id"], "decision_id": requirement.decision_id,
                    "pool_version": pool["pool_version"]}
        bound, request = max(found, key=lambda item: item[0])
        requirement = trial_admission_requirement(tenant, pid, cand["artifact_ref"], cand["checksum"], bound)
        ids = {"decision_id": requirement.decision_id, "request_id": request["request_id"], "pool_version": bound}
        status = str(request.get("status") or "")
        if status in self.CONSULT_FAILED_STATES:
            return {"state": "operator", "reason": "consultation_" + status, **ids}
        if status != "published":
            return {"state": "pending", **ids}
        memos = reader.list_memos(request_id=request["request_id"], status="published")
        if not memos:
            return {"state": "pending", **ids}
        check = None
        for memo in memos:
            check = verify_published_memo(reader, memo["memo_id"], tenant_id=tenant, persona_id=pid, requirement=requirement)
            if check.ok:
                return {"state": "verified", "memo_id": memo["memo_id"], **ids}
        return {"state": "operator", "reason": check.reason, **ids}

    def queue_operator_decision(self, persona, pool, cand, consult):
        """Turn the admission into an operator decision through the existing governance queue (evolution proposals)."""
        decision_id = "trial-admission-op-" + hashlib.sha256(
            f"{consult['decision_id']}|{consult['reason']}".encode()).hexdigest()[:24]
        body = {"decision_id": decision_id, "tenant_id": persona["tenant_id"], "target_type": "persona",
                "target_id": persona["persona_id"], "target_version": str(pool["pool_version"]),
                "action_type": "flag_for_review", "created_by_id": "persona-svc", "created_by_role": "evolution_controller",
                "persona_id": persona["persona_id"],
                "rationale": f"Paper trial admission of {cand['artifact_ref']} needs an operator decision: {consult['reason']}",
                "evidence_refs": [{"ref_type": "service_handoff", "ref_id": consult.get("request_id") or consult["decision_id"],
                                   "storage_ref": {"owner": "consultation-svc", "persona_id": persona["persona_id"],
                                                   "kind": "trial_admission_consultation"}}],
                "metadata": {"operator_required": True, "reason": consult["reason"], "candidate": {
                    "artifact_ref": cand["artifact_ref"], "checksum": cand["checksum"]},
                    "consultation_decision_id": consult["decision_id"], "paper_only": True}}
        try:
            self._call("POST", "evolution", "/api/evolution/proposals", persona, body)
        except Exception as exc:  # noqa: BLE001 - a repeated id is the existing queue entry, not a new one
            if getattr(exc, "code", None) != 409:
                raise
            return {"decision_id": decision_id, "status": "already_queued"}
        return {"decision_id": decision_id, "status": "queued"}

    def republish_pool_version(self, persona, pool, reasons):
        """Owner append of one version identical to ``pool`` for an envelope republish (joined_at carry)."""
        return self.pool_store.append_envelope_republish(
            persona["persona_id"], persona["tenant_id"], expected_version=pool["pool_version"],
            reasons=list(reasons), actor_id="persona-pool-cycle")

    def decide_pool(self, persona, body):
        import copy
        import importlib
        cand_ref = (body.get("candidate") or {}).get("artifact_ref") if isinstance(body, Mapping) else None
        if isinstance(body, Mapping) and body.get("action") == "admit" and cand_ref and callable(getattr(self.pool_store, "versions", None)):
            for ver in reversed(self.pool_store.versions(persona["persona_id"], persona["tenant_id"])):
                dec = ver.get("decision") or {}
                if dec.get("action") == "admit" and (dec.get("candidate") or {}).get("artifact_ref") == cand_ref:
                    return copy.deepcopy(ver)
        module = importlib.import_module("services.control_plane.persona.persona_strategy_pool")
        return self.pool_store.decide(persona["persona_id"], persona["tenant_id"],
                                      module.PoolDecisionRequest.model_validate(body), actor_id="persona-pool-cycle")

    def propose_evolution(self, persona, pool, member):
        from services.evolution.pool_evolution_scheduler import proposal
        entry = self._entry(persona, member["artifact_ref"])
        week = datetime.now(timezone.utc).isocalendar()
        kind = (entry.get("metadata") or {}).get("strategy_artifact", {}).get("strategy_logic", {}).get("kind")
        body = proposal({**pool, "tenant_id": persona["tenant_id"], "persona_id": persona["persona_id"]}, member, entry,
                        "mutate" if kind == "factor_rule_v1" else "retrain", f"week:{week.year}-{week.week}")
        result = self._call("POST", "evolution", "/api/evolution/proposals", persona, body)
        return {"status": "proposed", "decision_id": body["decision_id"], "response_status": (result or {}).get("decision_state")}

    def write_memory(self, persona, entry):
        pid = persona["persona_id"]
        failed = entry["outcome"] in ("admission_refused", "removed")
        tags = ["pool_cycle", entry["outcome"]] + ([f"tried_family:{entry['family']}"] if failed and entry.get("family") else [])
        payload = {"source_event_type": "pool_outcome", "source_event_id": f"{entry['packet_id']}:{entry['outcome']}:{entry.get('artifact_ref')}",
                   "write_authority": "persona-svc", "sponsor_persona_id": pid, "contributing_persona_ids": [pid],
                   "tenant_id": persona["tenant_id"], "summary": f"Pool {entry['outcome']}: {entry.get('artifact_ref')} {entry.get('detail', '')}".strip(),
                   "runtime_telemetry_evidence": [{"type": "pool_cycle", "id": entry["packet_id"], "artifact_ref": entry.get("artifact_ref")}],
                   "tags": tags}
        return self._call("POST", "memory", "/api/memory/writebacks/learn-feedback", persona, payload)

    def recall_tried_families(self, persona):
        out = self._call("GET", "memory", "/api/memory/entries?contributing_persona_id=" + quote(persona["persona_id"], safe="")
                         + "&session_persona_id=" + quote(persona["persona_id"], safe=""), persona)
        tried = set()
        for entry in (out or {}).get("entries", []):
            tags = (entry.get("content") or {}).get("tags") or entry.get("tags") or []
            tried |= {t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith("tried_family:")}
        return tried
