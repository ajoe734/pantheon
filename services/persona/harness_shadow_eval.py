"""Shadow comparison of a pre-checked harness patch against its base (design 6.8).

Recent frozen Observe bundles (the packet store's terminal daily-cycle packets)
are replayed through a base and a candidate harness. Replay is deny-all: the
provider receives a prompt only, no tool, owner route, memory write or broker;
this module never calls the risk owner, pool owner or any command route. The
only write is the comparison record in the existing harness owner.

Fail-closed rules: no/too few frozen bundles, an unchecked patch, or any
unavailable provider call yields an ``incomplete`` record. ``KEEP_BASE`` is the
result unless the comparison is complete and the candidate is not worse on any
recorded criterion and strictly better on at least one. Nothing here is
synthesized: bundles, proposals and book metrics all come from recorded data.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from services.persona.harness import (
    HarnessError, HarnessSurface, HarnessVersion, PersistentHarnessOwner, PersonaPatch, digest, precheck,
)

MIN_REPLAY_BUNDLES = 3
MAX_REPLAY_BUNDLES = 10          # per experiment; two model calls per bundle
MAX_COMPARISONS_PER_PATCH = 3
KIND_TO_CONSULT_ACTION = {"admit": "trial_admission"}
DENY_ALL_NOTICE = ("SHADOW REPLAY (deny-all): this is a frozen historical bundle. You have no tools. "
                   "Nothing you return is executed. Return the JSON proposal only.\n")


class DenyAllReplayProvider:
    """The only provider a replay accepts: a dedicated replay agent whose isolation is probed first.

    ``policy_probe(agent_id)`` must report the live agent policy; replay proceeds only when it is the
    dedicated replay agent (never the persona's own agent) with no tools and no side effects.
    ``turn_fn(agent_id, prompt)`` is the transport (``run_persona_ooda_turn`` bound to the gateway).
    """

    def __init__(self, agent_id: str, policy_probe: Callable[[str], Mapping[str, Any]], turn_fn: Callable):
        self.agent_id, self._probe, self._turn = agent_id, policy_probe, turn_fn

    def verify(self, persona_id: str) -> bool:
        if not self.agent_id or self.agent_id == persona_id:
            return False
        try:
            policy = self._probe(self.agent_id)
        except Exception:
            return False
        return (isinstance(policy, Mapping) and policy.get("agent_id") == self.agent_id
                and policy.get("tools") == [] and policy.get("side_effects") == "none")

    def run(self, prompt: str) -> Any:
        return self._turn(self.agent_id, prompt)


def _action(kind: str) -> str:
    return KIND_TO_CONSULT_ACTION.get(kind, kind)


def load_frozen_bundles(store_path: Path | str, tenant_id: str, persona_id: str,
                        limit: int = MAX_REPLAY_BUNDLES) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Newest terminal packets of this tenant/persona that carry a complete Observe bundle."""
    from services.persona.persona_cycle_controller import read_packets
    rows, skipped = [], {"no_observe_bundle": 0, "observe_incomplete": 0}
    for packet in read_packets(Path(store_path)).values():
        cycle = packet.get("persona_cycle") or {}
        if (packet.get("tenant_id") != tenant_id or persona_id not in packet.get("persona_ids", [])
                or cycle.get("phase") != "terminal"):
            continue
        bundle = cycle.get("observe_bundle")
        if not isinstance(bundle, dict) or not bundle:
            skipped["no_observe_bundle"] += 1
        elif bundle.get("unavailable") or bundle.get("persona_id") != persona_id:
            skipped["observe_incomplete"] += 1
        else:
            rows.append({"packet_id": packet["packet_id"], "updated_at": packet.get("updated_at", ""),
                         "bundle": copy.deepcopy(bundle)})
    rows.sort(key=lambda r: (r["updated_at"], r["packet_id"]), reverse=True)
    return rows[:limit], skipped


def _gates(surface: HarnessSurface, proposal: Any) -> tuple[list[str], list[str]]:
    """Deterministic gates for one proposal under one harness: (failures, required consult roles)."""
    if proposal.kind == "no_action":
        return [], []
    failures = []
    if len(proposal.evidence_refs) < surface.cycle_thresholds.minimum_evidence_count:
        failures.append("evidence_below_minimum")
    confidence = proposal.parameters.get("confidence")
    if type(confidence) not in (int, float) or isinstance(confidence, bool):
        failures.append("confidence_missing")
    elif confidence < surface.cycle_thresholds.minimum_confidence:
        failures.append("confidence_below_minimum")
    action = _action(proposal.kind)
    roles = sorted({r for t in surface.consult_policy if t.action == action for r in t.required_roles})
    if action in surface.guardrails.forbidden_solo_actions and not roles:
        failures.append("solo_forbidden_without_consult")
    return failures, roles


def _consult(roles: list[str], frozen: Mapping[str, Any]) -> str:
    """Outcome of the required roles from FROZEN recorded outcomes: n/a | approved | rejected | missing."""
    if not roles:
        return "n/a"
    verdicts = []
    for role in roles:
        entry = frozen.get(role)
        verdicts.append(entry.get("verdict") if isinstance(entry, Mapping) else None)
    if any(v == "reject" for v in verdicts):
        return "rejected"
    return "approved" if all(v == "approve" for v in verdicts) else "missing"


def _effect(proposal: Any, books: Mapping[str, Any]) -> float | None:
    """Virtual-book effect of one decision on the frozen trailing window, or None if unmeasurable."""
    member = proposal.parameters.get("member_id")
    book = books.get(member) if isinstance(member, str) else None
    if not isinstance(book, Mapping) or book.get("is_real_order") is not False:
        return None
    ret, count = book.get("total_return"), book.get("observation_count")
    if type(ret) not in (int, float) or type(count) is not int or count < 1:
        return None
    if proposal.kind == "remove":
        return -float(ret)
    delta = proposal.parameters.get("weight_delta")
    if proposal.kind == "reweight" and type(delta) in (int, float) and not isinstance(delta, bool):
        return float(delta) * float(ret)
    return None


class ProviderUnavailable(Exception):
    pass


def _replay(provider: DenyAllReplayProvider, persona: Mapping[str, Any], surface: HarnessSurface, label: str,
            version_row: Mapping[str, Any], bundle: Mapping[str, Any], usage: dict) -> dict[str, Any]:
    from integrations.openclaw.persona_ooda_runtime import CycleTurnOutput, build_daily_cycle_prompt
    observe = copy.deepcopy(dict(bundle))
    observe["harness"], observe["harness_version"] = dict(version_row), version_row.get("version")
    prompt = DENY_ALL_NOTICE + build_daily_cycle_prompt(persona, observe)
    started = time.monotonic()
    usage["model_calls"] += 1  # an attempt is usage whether or not it returns
    try:
        turn = provider.run(prompt)
    except Exception as exc:
        usage["failed_calls"] += 1
        usage["elapsed_ms"] += int((time.monotonic() - started) * 1000)
        raise ProviderUnavailable(type(exc).__name__) from None
    usage["elapsed_ms"] += int((time.monotonic() - started) * 1000)
    for key in ("input_tokens", "output_tokens"):
        value = getattr(turn, key, None)
        if type(value) is int:
            usage[key] = (usage.get(key) or 0) + value
    usage["models"] = sorted(set(usage["models"]) | {str(getattr(turn, "model", "unknown"))})
    if getattr(turn, "status", None) != "completed":
        raise ProviderUnavailable("turn_not_completed")
    try:
        output = CycleTurnOutput.model_validate_json(turn.decision)
    except ValidationError:
        return {"arm": label, "schema_valid": False, "proposals": [], "passed": 0, "total": 0,
                "consult_rejected": 0, "consult_missing": 0}
    rows = []
    frozen = bundle.get("consult_outcomes") or {}
    for proposal in output.proposals:
        failures, roles = _gates(surface, proposal)
        consult = _consult(roles, frozen) if not failures else "n/a"
        # Only gate-passing, consult-approved actions are effective; everything else is a no-op.
        effective = (proposal.kind != "no_action" and not failures and consult in ("n/a", "approved"))
        rows.append({"kind": proposal.kind, "target": proposal.parameters.get("member_id"),
                     "parameters": copy.deepcopy(proposal.parameters),
                     "gate_failures": failures, "consult_roles": roles, "consult_outcome": consult,
                     "effective": effective,
                     "effect": _effect(proposal, bundle.get("books") or {}) if effective else None})
    return {"arm": label, "schema_valid": True, "proposals": rows,
            "passed": sum(not r["gate_failures"] for r in rows), "total": len(rows),
            "consult_rejected": sum(r["consult_outcome"] == "rejected" for r in rows),
            "consult_missing": sum(r["consult_outcome"] == "missing" for r in rows)}


def _signature(arm: Mapping[str, Any]) -> list:
    """Effective decisions with their full parameters (a changed delta is a different decision)."""
    return sorted(json.dumps([r["kind"], r["parameters"]], sort_keys=True, default=str)
                  for r in arm["proposals"] if r["effective"])


def _rate(arms: list[Mapping[str, Any]]) -> float:
    # A schema-invalid turn counts as one failed proposal: it is not "no data".
    total = sum(max(a["total"], 1) for a in arms)
    return sum(a["passed"] for a in arms) / total if total else 0.0


def run_shadow_comparison(harness: PersistentHarnessOwner, persona_id: str, patch_id: str, *,
                          packet_store_path: Path | str, provider: DenyAllReplayProvider | None, actor: str,
                          max_replays: int = MAX_REPLAY_BUNDLES) -> dict[str, Any]:
    """Compare base and candidate on recorded bundles and record the result in the owner.

    ``provider`` must be a :class:`DenyAllReplayProvider` whose isolation probe passes; anything else
    is an incomplete comparison. Its turn returns a ``PersonaOodaTurn``-shaped object. Consult outcomes
    come only from the frozen bundle's ``consult_outcomes``; a required outcome that was not recorded
    leaves the comparison incomplete.
    """
    max_replays = max(1, min(int(max_replays), MAX_REPLAY_BUNDLES))
    persona = harness.personas.get(persona_id)
    state = harness.read(persona_id)
    row = (state.get("patches") or {}).get(patch_id)
    if row is None:
        raise HarnessError("patch_not_found", 404)
    if row["status"] != "previewed":
        raise HarnessError("precheck_required", 409)
    if len(row.get("comparisons", [])) >= MAX_COMPARISONS_PER_PATCH:
        raise HarnessError("comparison_budget_exhausted", 409)
    patch = PersonaPatch.model_validate(row["proposal"])
    if state["active_version"] != patch.base_version:
        raise HarnessError("stale_base_version", 409)
    base_row = HarnessVersion.model_validate(state["versions"][patch.base_version - 1])
    if base_row.content_digest != digest(base_row.surface.model_dump(mode="json")):
        raise HarnessError("base_content_digest_mismatch", 409)
    candidate, reasons = precheck(base_row.surface, patch, harness.script_root)
    if candidate is None or reasons:
        raise HarnessError("candidate_precheck_failed:" + ",".join(reasons), 409)
    candidate_row = {**base_row.model_dump(mode="json"), "version": "candidate",
                     "content_digest": digest(candidate.model_dump(mode="json")),
                     "surface": candidate.model_dump(mode="json")}

    bundles, skipped = load_frozen_bundles(packet_store_path, persona.tenant_id, persona_id, max_replays)
    usage = {"model_calls": 0, "elapsed_ms": 0, "models": [], "input_tokens": None, "output_tokens": None,
             "failed_calls": 0, "max_model_calls": 2 * max_replays}
    record: dict[str, Any] = {
        "schema_version": "persona_harness_shadow.v1", "persona_id": persona_id, "patch_id": patch_id,
        "base_version": patch.base_version, "base_digest": base_row.content_digest,
        "candidate_digest": candidate_row["content_digest"], "environment": "paper",
        "replay": {"deny_all_verified": False, "replay_agent_id": getattr(provider, "agent_id", None),
                   "side_effects": "none", "max_bundles": max_replays,
                   "min_bundles": MIN_REPLAY_BUNDLES, "bundle_packet_ids": [b["packet_id"] for b in bundles],
                   "skipped": skipped},
        "usage": usage, "bundles": [], "criteria": None, "status": "incomplete",
        "decision": "KEEP_BASE", "reasons": [],
    }
    persona_ctx = {"persona_id": persona_id, "mandate": getattr(persona, "mandate", None)}
    if len(bundles) < MIN_REPLAY_BUNDLES:
        record["reasons"].append("insufficient_frozen_bundles")
    elif provider is None:
        record["reasons"].append("provider_unavailable")
    elif not isinstance(provider, DenyAllReplayProvider) or not provider.verify(persona_id):
        record["reasons"].append("deny_all_provider_unverified")
    else:
        record["replay"]["deny_all_verified"] = True
        try:
            for item in bundles:
                arms = [_replay(provider, persona_ctx, surface, label, version, item["bundle"], usage)
                        for label, surface, version in (("base", base_row.surface, base_row.model_dump(mode="json")),
                                                        ("candidate", candidate, candidate_row))]
                base_arm, cand_arm = arms
                differs = _signature(base_arm) != _signature(cand_arm)
                measurable = None
                if differs:
                    eff = lambda arm: [r for r in arm["proposals"] if r["effective"]]
                    b_eff = [r["effect"] for r in eff(base_arm) if r["effect"] is not None]
                    c_eff = [r["effect"] for r in eff(cand_arm) if r["effect"] is not None]
                    unmeasured = any(r["effect"] is None for r in eff(base_arm) + eff(cand_arm)
                                     if r["kind"] in {"remove", "reweight"})
                    if (b_eff or c_eff) and not unmeasured:
                        measurable = sum(c_eff) - sum(b_eff)
                record["bundles"].append({"packet_id": item["packet_id"], "base": base_arm,
                                          "candidate": cand_arm, "decisions_differ": differs,
                                          "virtual_book_delta": measurable})
        except ProviderUnavailable as exc:
            record["reasons"].append("provider_unavailable:" + str(exc))
            record["bundles"] = []  # a partial replay is never evidence of a pass
    if not record["reasons"] and any(b[arm]["consult_missing"] for b in record["bundles"]
                                     for arm in ("base", "candidate")):
        record["reasons"].append("consult_outcomes_missing")
        record["bundles"] = []
    if not record["reasons"]:
        base_arms = [b["base"] for b in record["bundles"]]
        cand_arms = [b["candidate"] for b in record["bundles"]]
        deltas = [b["virtual_book_delta"] for b in record["bundles"] if b["virtual_book_delta"] is not None]
        crit = {"base_gate_pass_rate": _rate(base_arms), "candidate_gate_pass_rate": _rate(cand_arms),
                "consult_regressions": max(0, sum(b["candidate"]["consult_rejected"] - b["base"]["consult_rejected"]
                                          for b in record["bundles"])),
                "decisions_differing": sum(b["decisions_differ"] for b in record["bundles"]),
                "virtual_book_measured": len(deltas), "virtual_book_delta_total": sum(deltas)}
        record["criteria"], record["status"] = crit, "complete"
        not_worse = (crit["candidate_gate_pass_rate"] >= crit["base_gate_pass_rate"]
                     and crit["consult_regressions"] == 0 and crit["virtual_book_delta_total"] >= 0)
        better = (crit["candidate_gate_pass_rate"] > crit["base_gate_pass_rate"]
                  or crit["virtual_book_delta_total"] > 0)
        if not_worse and better:
            record["decision"] = "CANDIDATE_BETTER"
            record["reasons"].append("candidate_better_on_recorded_criteria")
        else:
            record["reasons"].append("candidate_not_better_keep_base")
    harness.record_comparison(persona_id, patch_id, record, actor)
    return record
