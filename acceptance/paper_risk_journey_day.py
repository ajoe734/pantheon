#!/usr/bin/env python3
"""Daily, read-only judgement of the currently served release's paper risk journey samples.

Release time (acceptance/probes/paper-risk-journey) reports missing fresh samples as pending (77).
This check runs after the Taiwan session from persona-day-acceptance.yml, reuses the probe's ``check``
unchanged for every sample check, and turns "pending" into a deadline. Trading sessions are the distinct
trade days of the decisions released for the configured personas, read from the existing trade-journey
route and the immutable decision events (never a calendar route, never weekday arithmetic). Operator HOLD
rule (2026-11-25): a decision the strategy itself held is neither pass nor failure; a directional view
without pre_trade_sizing, or a HOLD with candidates_unavailable / budget_unavailable, fails at once;
five trading days without a judgeable decision for a persona fail; seven calendar days without any
released decision fail. GET only after the viewer token exchange (owner reads use the release stage's
pinned SSH transport); writes nothing but its stdout artifact. See acceptance/README.md.

Exit: 0 passed or pending, 1 failed, 77 missing configuration. The verdict is in the JSON ``verdict``.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

_loader = importlib.machinery.SourceFileLoader(
    "paper_risk_journey_probe", str(Path(__file__).with_name("probes") / "paper-risk-journey"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
probe = importlib.util.module_from_spec(_spec)
_loader.exec_module(probe)
binding = probe.binding
JOURNEYS_PER_PERSONA = 6  # newest journeys read per persona: one detail + one source read each
binding.MAX_REQUESTS = 72 + 3 * (1 + 2 * JOURNEYS_PER_PERSONA) + 2  # probe + scan + manifest reads
binding.MAX_SECONDS = 280

TRADING_SESSIONS_PER_PERSONA = 5
FILL_OR_REFUSAL_SESSIONS = 5
NO_DECISION_DAYS = 7
TAIPEI = timezone(timedelta(hours=8))
STRATEGY_HOLD_REASONS = {"flat_view", "already_at_target"}
LATE_FINDINGS = ("no_fresh_pre_broker_rejection_with_amounts", "no_fresh_broker_position_cash_reconciled_fill")


def classify(event, policy, cutoff):
    """One immutable trade_decision event -> dict with ``class`` in held|sized|failed|before_cutoff."""
    meta = event.get("metadata") or {}
    try:
        if meta.get("decision_time"):
            decided = probe.timestamp(meta["decision_time"])
        else:
            decided = datetime.fromisoformat(str(meta["trade_date"])).replace(tzinfo=TAIPEI)
    except (KeyError, ValueError, TypeError):
        return {"class": "failed", "reason": "decision_time_unreadable"}
    day = str(meta.get("trade_date") or decided.astimezone(TAIPEI).date().isoformat())[:10]
    out = {"event_id": event.get("event_id"), "decision_time": decided.isoformat(), "trading_day": day}
    if decided <= cutoff:
        return {**out, "class": "before_cutoff"}
    reason = str(meta.get("reason_code") or "")
    detail = str(meta.get("detail") or (meta.get("expression_decision") or {}).get("detail") or "")
    if reason == "candidates_unavailable" or "budget_unavailable" in detail:
        return {**out, "class": "failed",
                "reason": "budget_unavailable" if "budget_unavailable" in detail else "candidates_unavailable"}
    direction = str(meta.get("view_direction") or "").upper()
    if reason in STRATEGY_HOLD_REASONS:
        return {**out, "class": "held", "reason": reason}
    if direction in ("LONG", "SHORT") or str(event.get("action") or meta.get("action") or "").upper() in ("BUY", "SELL"):
        sizing = meta.get("pre_trade_sizing")
        if not isinstance(sizing, dict) or not sizing.get("binding_limit"):
            return {**out, "class": "failed", "reason": "directional_view_without_pre_trade_sizing"}
        if not probe.sized(event, policy, "pre_trade_sizing"):
            return {**out, "class": "failed", "reason": "pre_trade_sizing_not_bound_to_policy"}
        return {**out, "class": "sized"}
    return {**out, "class": "held", "reason": reason or "no_directional_view"}


def scan(fetch, source_read, report, now):
    """Read released decisions from the trade-journey route. Raises nothing: unreadable reads are listed."""
    cutoff = probe.timestamp(report["deployment_cutoff"])
    found = {"decisions": [], "ignored_before_cutoff": [], "unreadable": []}
    for pid, owner in sorted(report["policy_readbacks"]["personas"].items()):
        tenant = owner.get("tenant_id", "tenant-dev")
        query = "?" + urllib.parse.urlencode({"tenant_id": tenant, "environment": "paper",
                                              "persona_id": pid, "page_size": 50})
        try:
            items = probe.binding.data(fetch(probe.BASE + query)).get("items")
            if not isinstance(items, list):
                raise ValueError("journey_page_invalid")
        except (RuntimeError, ValueError) as exc:
            found["unreadable"].append({"persona_id": pid, "read": "journey_list", "error": str(exc)})
            continue
        items = [i for i in items if isinstance(i, dict) and i.get("persona_id") == pid
                 and i.get("persona_is_probe") is False and i.get("journey_id") and i.get("created_at")]
        items.sort(key=lambda i: i["created_at"], reverse=True)
        for item in items[:JOURNEYS_PER_PERSONA]:
            jid = item["journey_id"]
            try:
                row = probe.binding.data(fetch(probe.BASE + "/" + urllib.parse.quote(jid, safe="") + "?tenant_id="
                                               + urllib.parse.quote(tenant, safe="") + "&environment=paper"))
                stub = (row.get("stage_events") or {}).get("trade_decision") or {}
                if not stub or probe.timestamp(stub.get("occurred_at", "")) <= cutoff:
                    continue
                if stub.get("source_mode") != "live" or stub.get("accepted_live") is not True:
                    continue  # not released
                eid = stub.get("source_event_id") or stub.get("canonical_event_id") or stub.get("event_id")
                event = probe.binding.data(source_read(eid, tenant))
            except (RuntimeError, ValueError, KeyError, AttributeError, TypeError) as exc:
                found["unreadable"].append({"persona_id": pid, "journey_id": jid, "error": str(exc)})
                continue
            if (event.get("event_id") != eid or event.get("event_type") != "trade_decision"
                    or (event.get("authority_refs") or {}).get("persona_id") != pid
                    or event.get("binding_id") != owner.get("binding_id")
                    or (event.get("metadata") or {}).get("fixture_source")):
                found["unreadable"].append({"persona_id": pid, "journey_id": jid, "error": "decision_identity_mismatch"})
                continue
            result = {"persona_id": pid, "journey_id": jid, **classify(event, owner.get("effective_policy") or {}, cutoff)}
            if result["class"] == "before_cutoff":
                found["ignored_before_cutoff"].append(result)
            else:
                found["decisions"].append(result)
    return found


def judge(report, found, now):
    """Pure verdict from the probe report and the scan; every failure names persona, decision and reason."""
    failed = list(report.get("failed") or [])
    cutoff = probe.timestamp(report["deployment_cutoff"])
    decisions = found["decisions"]
    days = sorted({d["trading_day"] for d in decisions if d.get("trading_day")})
    out = {"verdict": "passed", "failed": failed, "pending": [], "reasons": [], "trading_sessions": len(days),
           "trading_days": days, "decisions": decisions, "ignored_before_cutoff": found["ignored_before_cutoff"],
           "unreadable": found["unreadable"]}
    reasons = []
    if [f for f in failed if not probe.is_no_sample(f)]:
        reasons.append("non_sample_findings")
    for d in decisions:
        if d["class"] == "failed":
            reasons.append("%s:%s:%s" % (d["persona_id"], d.get("event_id") or d["journey_id"], d["reason"]))
    for pid in sorted(report["policy_readbacks"]["personas"]):
        mine = [d for d in decisions if d["persona_id"] == pid]
        if len(days) >= TRADING_SESSIONS_PER_PERSONA and not any(d["class"] == "sized" for d in mine):
            reasons.append("%s:no_judgeable_decision_after_%d_trading_days" % (pid, TRADING_SESSIONS_PER_PERSONA))
    if len(days) >= FILL_OR_REFUSAL_SESSIONS and any(f in LATE_FINDINGS for f in failed):
        reasons.append("no_refusal_or_reconciled_fill_after_%d_trading_days" % FILL_OR_REFUSAL_SESSIONS)
    if not decisions and now - cutoff >= timedelta(days=NO_DECISION_DAYS):
        reasons.append("no_released_decision_after_%d_days" % NO_DECISION_DAYS)
    if found["unreadable"] and now - cutoff >= timedelta(days=NO_DECISION_DAYS):
        reasons.append("journey_reads_unreadable_after_%d_days" % NO_DECISION_DAYS)
    if reasons:
        out.update(verdict="failed", reasons=reasons, pending=[f for f in failed if probe.is_no_sample(f)])
    elif failed or found["unreadable"]:
        out.update(verdict="pending", pending=[f for f in failed if probe.is_no_sample(f)],
                   reasons=["journey_reads_unreadable"] if found["unreadable"] else [])
    return out


def evaluate(fetch, manifest_fetch, source_read, *, expected, pool_policy_reader=None, now=None):
    now = now or datetime.now(timezone.utc)
    manifest = manifest_fetch()
    report = probe.check(fetch, lambda: manifest_fetch(), source_read, expected=expected,
                         backend_sha=str(manifest.get("bffCommit", "")), frontend_sha=str(manifest.get("frontendSha", "")),
                         pool_policy_reader=pool_policy_reader, now=now)
    # The sample checks and their report stay as the probe produced them; only the deadline is added.
    report["day"] = judge(report, scan(fetch, source_read, report, now), now)
    report["verdict"] = report["day"]["verdict"]
    report["exit_code"] = 1 if report["verdict"] == "failed" else 0
    report["passed"] = report["verdict"] == "passed"
    return report


def main():
    def runner(fetch, expected, pool_reader, public_read, owner_read):
        captured["report"] = evaluate(fetch, public_read, lambda eid, tenant: owner_read("telemetry", eid, tenant),
                                      expected=expected, pool_policy_reader=pool_reader)
        return captured["report"]
    captured = {}
    if not os.environ.get("ACCEPTANCE_FE_URL"):
        print("skipped: missing ACCEPTANCE_FE_URL")
        return 77
    code = binding.main(runner)
    return captured["report"]["exit_code"] if "report" in captured else code


if __name__ == "__main__":
    sys.exit(main())
