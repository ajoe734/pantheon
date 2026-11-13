"""Daily Observe-Orient-Decide-Act-Learn cycle of one persona over its strategy pool.

Runs inside the existing persona daily-cycle consumer (``PersonaCycleController``)
and fills that cycle's OODA record; there is no second schedule. The decision is
deterministic policy over the observed pool health, never a model output, and
every action goes through an existing owner route, paper only:

  open_demand      research owner  POST /api/research/persona-demands
  admit_trial      persona owner   POST .../strategy-pool/decisions (admit)
  reweight/remove  persona owner   POST .../strategy-pool/decisions (reweight; the
                                   owner removes members past its drawdown rule)
  propose_mutate   evolution owner proposal (same builder as the pool scheduler)
  propose_widen    staged only: no permitted-universe owner exists yet, so no
                   authority to widen is inferred
  no_action        reason recorded

Outcomes are written to the persona's own memory (``pool_outcome``) with
``tried_family:<id>`` tags; the next demand excludes those families.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote
from urllib.request import Request, urlopen

DECISIONS = ("open_demand", "admit_trial", "reweight", "remove", "propose_mutate", "propose_widen", "no_action")
MEASURE_BY_REASON = {"low_risk_budget_utilisation": "gross_utilisation",
                     "too_few_independent_bets": "effective_bets"}
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
        # Learn
        self._learn(persona, learned, packet_id, memory_errors)
        learned = pre_learned + learned
        return {"status": "completed", "observe": {"pool_version": pool["pool_version"], "members": len(pool["members"]),
                "health": health}, "orient": orient,
                "decide": {k: v for k, v in decision.items() if k not in ("candidate", "member")}
                         | ({"candidate_ref": decision["candidate"]["artifact_ref"]} if "candidate" in decision else {}),
                "act": {"receipts": receipts, "paper_only": True}, "learn": {"memory_entries": [e["outcome"] for e in learned], "memory_write_errors": memory_errors,
                "tried_families": sorted({e["family"] for e in learned if e.get("family")})}}

    def _learn(self, persona, entries, packet_id, errors):
        for entry in entries:
            try:
                self.ports.write_memory(persona, entry | {"packet_id": packet_id})
            except Exception as exc:  # noqa: BLE001 - the action already happened; record, do not hide
                errors.append(f"{entry['outcome']}:{type(exc).__name__}")

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
            try:
                dataset = ports.admission_dataset(persona, cand)
            except Exception as exc:  # noqa: BLE001 - missing data fails closed, never synthetic
                receipts.append({"route": "persona:strategy-pool/decisions", "action": "admit", "status": "not_attempted",
                                 "reason": "governed_dataset_unavailable:" + type(exc).__name__})
                return receipts, learned
            try:
                cand_book = ports.candidate_book(persona, cand, dataset)
            except Exception as exc:  # noqa: BLE001 - no replay, no admission; never fabricate returns
                receipts.append({"route": "persona:strategy-pool/decisions", "action": "admit", "status": "not_attempted",
                                 "reason": "candidate_book_unavailable:" + type(exc).__name__})
                return receipts, learned
            result = ports.decide_pool(persona, {
                "expected_version": pool["pool_version"], "execution_context": "paper", "action": "admit",
                "candidate": {"artifact_ref": cand["artifact_ref"], "checksum": cand["checksum"],
                              **({"demand_ref": cand["demand_ref"]} if cand.get("demand_ref") else {})},
                "books": {**observe.get("books", {}), cand["artifact_ref"]: cand_book},
                "observations": observe.get("observations", []), "dataset": dataset})
            status = (result.get("decision") or {}).get("status")
            receipts.append({"route": "persona:strategy-pool/decisions", "action": "admit", "status": status,
                             "pool_version": result.get("pool_version"), "reasons": (result.get("decision") or {}).get("reasons", [])})
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

    Reuses admission's replay (same registry interpreter, previous-bar exposure and
    Taiwan-cost netting) over the untouched window of the governed daily bars.
    Nothing is invented: unavailable or misaligned data raises.
    """
    from services.execution.lean_runtime.member_virtual_book import NET_BASIS
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
            "dates": dates[1:], "returns": returns, "since_inception": True, "book_kind": "governed_replay"}


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


class HttpPoolCyclePorts:
    """Owner HTTP ports. ``transport(method, service, path, tenant, body)`` is the only I/O."""

    SERVICE_URL_ENV = {"persona": ("PERSONA_API_URL", "http://persona:8002"),
                       "research": ("RESEARCH_API_URL", "http://research-orchestrator:8100"),
                       "memory": ("MEMORY_API_URL", "http://memory:8086"),
                       "evolution": ("EVOLUTION_API_URL", "http://evolution:8093"),
                       "registry": ("REGISTRY_URL", "http://registry:8087")}

    # Owner-specific credentials already configured for the persona service; the
    # persona owner itself is called in-process through ``pool_store`` (no self-HTTP).
    TOKEN_VARIABLE = {"research": "PERSONA_RESEARCH_SERVICE_TOKEN", "memory": "PANTHEON_MEMORY_AUTH_TOKEN",
                      "evolution": "EVOLUTION_AUTH_TOKEN", "registry": "PERSONA_REGISTRY_SERVICE_TOKEN"}

    def __init__(self, transport: Callable[..., Any] | None = None, *, pool_store: Any = None,
                 dataset_loader: Callable[[Mapping, Mapping], dict] | None = None, env: Mapping[str, str] | None = None):
        self.env = os.environ if env is None else env
        self.transport = transport or self._urllib
        self.pool_store = pool_store
        self.dataset_loader = dataset_loader
        self._entries: dict[tuple[str, str], Mapping] = {}

    def _urllib(self, method, service, path, tenant, body=None):
        from services.service_token_file import configured_service_token
        name, default = self.SERVICE_URL_ENV[service]
        token = configured_service_token(self.TOKEN_VARIABLE[service], self.env)
        if not token:  # fail closed: never send an empty bearer
            raise RuntimeError(f"owner_credential_unconfigured:{service}")
        request = Request(self.env.get(name, default).rstrip("/") + path, method=method,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant,
                                   "Content-Type": "application/json"})
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

    def candidates(self, persona, pool):
        """Gate-passed StrategyArtifact candidates resolved through owner evidence.

        alpha-replication entries describe a reviewed StrategySpec (its id/checksum),
        not the executable artifact. Only a completed, un-failed research entry is
        followed to the registry's approved execution bundle whose lineage names that
        spec and whose own gate proof resolves; anything else is skipped so an unusable
        entry can never starve valid supply.
        """
        from services.evolution.approved_baselines import resolve_member_baseline
        rows = self._call("GET", "research", "/api/research-orchestrator/alpha-replication/entries", persona)
        in_pool = {m["artifact_ref"] for m in (pool or {}).get("members", []) if m.get("stage") != "removed"}
        out, seen = [], set()
        for row in rows or []:
            spec_id, family = row.get("strategy_spec_id"), row.get("strategy_id")
            if (row.get("admission_status") or row.get("status")) != "admitted" or row.get("queue_status") != "completed" \
                    or row.get("last_error") or not spec_id or not family:
                continue
            try:
                versions = self._call("GET", "registry", "/api/registry/strategies/" + quote(str(family), safe="") + "/entries", persona)
            except Exception:  # noqa: BLE001 - unreadable owner evidence is not a candidate
                continue
            for entry in versions or []:
                ref = entry.get("registry_id")
                artifact = (entry.get("metadata") or {}).get("strategy_artifact")
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
                            "family": artifact.get("strategy_id") or family, "demand_ref": row.get("demand_ref")})
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
        return self.dataset_loader(persona, candidate)

    def candidate_book(self, persona, candidate, dataset):
        """Candidate's governed replay book; the telemetry owner has none before admission."""
        from services.control_plane.persona.pool_policy import load_policy
        artifact = (self._entry(persona, candidate["artifact_ref"]).get("metadata") or {}).get("strategy_artifact")
        if not artifact:
            raise RuntimeError("candidate_strategy_artifact_missing")
        return candidate_virtual_book(dataset=dataset, artifact=artifact, checksum=candidate["checksum"], policy=load_policy())

    def decide_pool(self, persona, body):
        import importlib
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
        out = self._call("GET", "memory", "/api/memory/entries?contributing_persona_id=" + quote(persona["persona_id"], safe=""), persona)
        tried = set()
        for entry in (out or {}).get("entries", []):
            tags = (entry.get("content") or {}).get("tags") or entry.get("tags") or []
            tried |= {t.split(":", 1)[1] for t in tags if isinstance(t, str) and t.startswith("tried_family:")}
        return tried
