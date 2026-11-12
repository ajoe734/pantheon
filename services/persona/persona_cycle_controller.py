"""Persona OODA reconciliation, hosted by the existing persona process.

OpenClaw owns time and run identities. This consumer never force-runs a job,
calculates a due time, or retries an uncertain agent/action attempt. A durable
observing snapshot is the claim; restart turns an interrupted claim into an
explicit terminal failure rather than buying another model turn.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote
from urllib.request import Request, urlopen

from integrations.openclaw.persona_agent_sync import active_harness_version
from integrations.openclaw.persona_ooda_runtime import (
    CycleTurnOutput, build_daily_cycle_prompt, run_persona_ooda_turn,
)
from services.control_plane.cron.persona_cron_registrar import AdapterCronRuntime, PersonaCronRegistrar
from services.control_plane.cron.workflows import PERSONA_DAILY_CYCLE_WORKFLOW_ID as WORKFLOW_ID
from services.control_plane.ooda.jsonl_store import OodaJsonlAppendStore
from services.control_plane.ooda.ooda_loop_packet import OodaLoopPacket
from services.persona.cron_ooda_closure import append_ooda_packet, resolve_ooda_packet_store_path, _utc_now

log = logging.getLogger(__name__)
CONTROLLER_NAME = "persona_cycle_controller"
ELIGIBLE_STATES = {"paper_running", "paper_owner", "consultable"}


def packet_identity(persona: Mapping[str, Any], job_id: str, run: Mapping[str, Any]) -> str:
    identity = run.get("runId")
    if not identity:
        # The pinned Gateway often supplies runAtMs rather than runId.
        identity = run.get("runAtMs")
        if type(identity) is not int or identity < 0:
            raise ValueError("terminal cron run lacks a stable run identity")
    key = [persona["tenant_id"], persona["persona_id"], WORKFLOW_ID, job_id, identity]
    return "ooda-persona-" + hashlib.sha256(json.dumps(key).encode()).hexdigest()


def read_packets(path: Path) -> dict[str, dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return {p["packet_id"]: p for p in OodaJsonlAppendStore(path).list_packets()}
    if not path.exists():
        return {}
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError("OODA JSON store must be an array of record envelopes")
    return {r["packet_id"]: r["payload"] for r in rows if r.get("record_type") == "packet_snapshot"}


def cycle_health(packet: dict[str, Any] | None) -> dict[str, Any]:
    if not packet:
        return {"status": "unobserved", "last_packet_id": None, "stage": None,
                "failure_reason": "no_daily_cycle_packet"}
    cycle = packet.get("persona_cycle") or {}
    return {"status": "failed" if cycle.get("failure_reason") else
            ("running" if cycle.get("phase") == "claimed" else "completed"),
            "last_packet_id": packet["packet_id"], "stage": packet["status"],
            "failure_reason": cycle.get("failure_reason"), "updated_at": packet["updated_at"],
            "harness_version": cycle.get("harness_version"),
            "risk_status": (cycle.get("risk_result") or {}).get("status", "unobserved")}


class PersonaCycleController:
    def __init__(self, registrar: PersonaCronRegistrar, owners: Any, *,
                 store_path: Path | str | None = None, turn_runner: Callable = run_persona_ooda_turn):
        self.registrar, self.owners, self.turn_runner = registrar, owners, turn_runner
        self.path = Path(store_path) if store_path else resolve_ooda_packet_store_path()

    def consume_run(self, persona: dict[str, Any], job_id: str, run: dict[str, Any]) -> dict[str, Any]:
        if persona.get("lifecycle_state") not in ELIGIBLE_STATES or not persona.get("tenant_id"):
            raise ValueError("persona is not eligible or has no authoritative tenant")
        if run.get("status") not in {"ok", "error", "skipped"}:
            raise ValueError("cron run is not terminal")
        if run.get("jobId") not in (None, job_id):
            raise ValueError("cron run belongs to another job")
        packet_id = packet_identity(persona, job_id, run)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Cross-process fencing over the shared packet volume. A kernel lock
        # is released on crash; the persisted claim is deliberately not a TTL
        # lease that could permit a second model turn after a slow response.
        with self.path.with_suffix(self.path.suffix + ".cycles.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = read_packets(self.path).get(packet_id)
            if existing:
                if existing.get("persona_cycle", {}).get("phase") == "claimed":
                    existing["persona_cycle"].update(phase="terminal", failure_reason="interrupted_cycle_outcome_unknown")
                    existing["updated_at"] = _utc_now()
                    append_ooda_packet(self.path, existing)
                return existing
            now = _utc_now()
            packet = OodaLoopPacket(packet_id=packet_id, loop_type="paper_strategy", status="observing",
                                    environment="paper", persona_ids=[persona["persona_id"]],
                                    created_at=now, updated_at=now).to_dict()
            packet.update(tenant_id=persona["tenant_id"], producer={
                "channel": "openclaw_cron_dispatch", "workflow_id": WORKFLOW_ID,
                "cron_job_id": job_id, "cron_run_id": run.get("runId"),
                "cron_run_at_ms": run.get("runAtMs"), "cron_run_status": run["status"],
                "fabricated": False}, persona_cycle={"phase": "claimed", "failure_reason": None})
            packet["observe"]["source_refs"] = [f"cron-job://{job_id}"]
            packet["observe"]["telemetry_refs"] = [f"cron-run://{job_id}/{run.get('runId') or run.get('runAtMs')}"]
            append_ooda_packet(self.path, packet)  # durable before any external side effect
            cycle = packet["persona_cycle"]
            observe: dict[str, Any] = {}
            failure = None
            try:
                observe = self.owners.observe(persona)
                cycle["observe_bundle"] = observe
                for key in ("telemetry_refs", "incident_refs", "human_feedback_refs"):
                    packet["observe"][key].extend(observe.get(key, []))
                cycle["harness_version"] = observe.get("harness_version")
            except Exception as exc:
                failure = f"observe_unavailable:{type(exc).__name__}"
            # Independent of model, harness, memory and provider availability.
            try:
                cycle["risk_result"] = self.owners.risk(persona, observe, packet_id)
            except Exception as exc:
                cycle["risk_result"] = {"status": "unavailable", "reason": type(exc).__name__}
            if run["status"] != "ok":
                failure = "cron_terminal_" + run["status"]
            elif not failure and observe.get("unavailable"):
                failure = "observe_prerequisites_unavailable:" + ",".join(observe["unavailable"])
            if not failure:
                try:
                    turn = self.turn_runner(persona["persona_id"], build_daily_cycle_prompt(persona, observe))
                    cycle["turn"] = turn.to_dict()
                    if turn.status != "completed" or turn.persona_id != persona["persona_id"]:
                        raise ValueError("agent_turn_unavailable")
                    output = CycleTurnOutput.model_validate_json(turn.decision)
                    cycle["output"] = output.model_dump(mode="json")
                    packet["orient"]["evidence_bundle_refs"] = [f"{packet_id}#persona_cycle/output"]
                    packet["status"] = "oriented"
                    cycle["decisions"] = []
                    # Only the existing deterministic risk decision may reweight
                    # or remove. Every other proposal stays staged until its
                    # owner verifies evidence, consultation and authority.
                    for proposal in output.proposals:
                        result = {"kind": proposal.kind, "status": "requires_owner_gate",
                                  "reason": "owner_evidence_and_consultation_required"}
                        if proposal.kind == "no_action":
                            result.update(status="no_action", reason=proposal.reason)
                        elif proposal.kind in {"reweight", "remove"}:
                            result.update(status="deterministic_risk_only", result=cycle["risk_result"])
                        cycle["decisions"].append(result)
                    packet["decide"]["decision_rationale_ref"] = f"{packet_id}#persona_cycle/decisions"
                    packet["decide"]["sponsor_persona_id"] = persona["persona_id"]
                    packet["status"] = "decided"
                    risk = cycle["risk_result"]
                    if risk.get("receipt_ref"):
                        packet["act"]["command_receipt_refs"] = [risk["receipt_ref"]]
                    else:
                        # This is a receipt for staging/no-op, not an execution
                        # receipt or fabricated downstream owner acknowledgement.
                        packet["act"]["command_receipt_refs"] = [f"{packet_id}#persona_cycle/decisions"]
                    packet["act"]["safe_mode_refs"] = ["policy://persona-cycle/proposal-only"]
                    packet["learn"]["telemetry_refs"] = [f"{packet_id}#persona_cycle/output/memory_candidates"]
                    packet["status"], packet["closed_at"] = "closed", _utc_now()
                except Exception as exc:
                    # Never expose transport exception strings (which may
                    # include credentials or private response bodies).
                    failure = "agent_turn_or_schema_unavailable:" + type(exc).__name__
            cycle.update(phase="terminal", failure_reason=failure)
            if failure:
                packet["status"] = "observing"
                packet["audit_refs"].append("persona-cycle-failure:" + failure)
            packet["updated_at"] = _utc_now()
            errors = OodaLoopPacket.from_dict(packet).validate()
            if errors:
                raise ValueError(f"daily-cycle packet failed schema invariants: {errors}")
            append_ooda_packet(self.path, packet)
            return packet

    def reconcile_once(self) -> dict[str, Any]:
        personas = self.owners.personas()  # fail closed before any cron deletion
        report = self.registrar.reconcile_daily_cycles(personas)
        runtime = self.registrar._get_runtime()
        by_id = {p["persona_id"]: p for p in personas}
        report["personas"] = {}
        for pid, job_id in report["jobs"].items():
            try:
                listing = runtime.gateway_call("cron.runs", {"id": job_id, "limit": 100})
                if listing.get("hasMore"):
                    raise RuntimeError("cron_run_backlog_exceeds_bounded_batch")
                runs = listing.get("entries")
                if not isinstance(runs, list):
                    raise ValueError("invalid cron.runs payload")
                packets = []
                for run in sorted(runs, key=lambda r: r.get("runAtMs", 0)):
                    if run.get("status") in {"ok", "error", "skipped"}:
                        packets.append(self.consume_run(by_id[pid], job_id, run))
                latest = packets[-1] if packets else None
                report["personas"][pid] = cycle_health(latest)
            except Exception as exc:
                report["failures"].append({"persona_id": pid, "reason": str(exc)[:300]})
        return report


class CycleOwners:
    """Fixed product-owner ports. No caller/model-selected URLs or command routes."""
    def __init__(self, persona_owner: Any, pool_store: Any, env=None):
        self.persona_owner, self.pool_store = persona_owner, pool_store
        self.env = os.environ if env is None else env

    def personas(self):
        return self.persona_owner.list_json()

    def _get(self, url, token, tenant):
        if not url or not token:
            raise RuntimeError("owner_read_unconfigured")
        request = Request(url, headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def observe(self, persona):
        pid, tenant = persona["persona_id"], persona["tenant_id"]
        version, reason = active_harness_version(persona)
        bundle = {"persona_id": pid, "tenant_id": tenant, "harness": version,
                  "harness_version": version["version"] if version else None,
                  "books": {}, "observations": [], "telemetry_refs": [], "incident_refs": [],
                  "human_feedback_refs": [], "unavailable": []}
        if not version:
            bundle["unavailable"].append(reason)
        # Canonical private memory is refreshed on the Gateway workspace before
        # every turn, using the already-authenticated adapter materializer.
        try:
            base = self.env["PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL"].rstrip("/")
            request = Request(base + "/api/openclaw-adapter/agents/persona/memory/materialize",
                data=json.dumps({"persona_id": pid, "workspace_ref": persona["workspace_ref"]}).encode(),
                headers={"Content-Type": "application/json", "X-Pantheon-Service-Token":
                         self.env["PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN"]})
            with urlopen(request, timeout=30) as response:
                memory = json.load(response)
            if memory.get("status") != "ok" or not isinstance(memory.get("memory"), dict):
                raise ValueError("invalid memory receipt")
            bundle["retrieved_memory"] = memory["memory"]
        except Exception:
            bundle["unavailable"].append("canonical_memory")
        try:
            base = self.env.get("PANTHEON_TELEMETRY_API_URL", "").rstrip("/")
            payload = self._get(base + "/api/telemetry/runtime-summaries",
                                self.env.get("PANTHEON_TELEMETRY_SERVICE_TOKEN"), tenant)
            rows = [r for r in payload["summaries"] if r.get("persona_id") == pid and r.get("tenant_id") == tenant]
            bundle["telemetry"] = rows
            if len(rows) != 1 or rows[0].get("deployment_stage") != "paper":
                raise ValueError("ambiguous or missing own paper telemetry")
            row = rows[0]
            bundle["telemetry_refs"] = [base + "/api/telemetry/runtime-summaries/" + quote(row["runtime_id"], safe="")]
            books = row.get("member_virtual_books") or []
            bundle["books"] = {b.get("member_id") or b["artifact_id"]: {**b, **b.get("net_of_costs", {})} for b in books}
            bundle["observations"] = row.get("pool_observations") or []
            bundle["incident_refs"] = row.get("incident_refs") or []
            bundle["human_feedback_refs"] = row.get("human_feedback_refs") or []
        except Exception:
            bundle["unavailable"].append("own_telemetry")
        if persona["lifecycle_state"] != "consultable":
            try:
                bundle["pool"] = self.pool_store.current(pid, tenant)
                bundle["pool_health"] = self.pool_store.health(pid, tenant, bundle["books"], bundle["observations"])
            except Exception:
                bundle["unavailable"].append("strategy_pool")
        return bundle

    def risk(self, persona, observe, packet_id):
        if persona["lifecycle_state"] == "consultable":
            return {"status": "not_applicable", "reason": "non_trading_persona"}
        # Calling the same application owner as the authenticated HTTP route
        # avoids a self-HTTP dependency. The owner validates books, policy,
        # expected version and registry proof; it never publishes execution.
        module = importlib.import_module("services.control_plane.persona.persona_strategy_pool")
        pool = self.pool_store.current(persona["persona_id"], persona["tenant_id"])
        request = module.PoolDecisionRequest(expected_version=pool["pool_version"],
            execution_context="paper", action="reweight", books=observe.get("books", {}),
            observations=observe.get("observations", []))
        result = self.pool_store.decide(persona["persona_id"], persona["tenant_id"], request,
                                        actor_id=CONTROLLER_NAME)
        return {"status": result["decision"]["status"], "decision": result["decision"],
                "receipt_ref": f"/api/personas/{quote(persona['persona_id'], safe='')}/strategy-pool/versions/{result['pool_version']}"}


async def run_controller(controller: PersonaCycleController, stop: asyncio.Event, *, interval_seconds=30):
    """Poll actual cron records; this interval never authorizes a persona turn."""
    writers = {}
    while not stop.is_set():
        try:
            report = await asyncio.to_thread(controller.reconcile_once)
            personas = controller.owners.personas()
            for tenant in sorted({p["tenant_id"] for p in personas if p.get("tenant_id")}):
                if tenant not in writers:
                    writers[tenant] = importlib.import_module("services.loop-control").LoopControllerWriter(
                        os.environ["DATABASE_URL"], tenant_id=tenant, controller_name=CONTROLLER_NAME,
                        lease_duration_seconds=300)
                owned = {p["persona_id"] for p in personas if p.get("tenant_id") == tenant}
                health = {pid: h for pid, h in report["personas"].items() if pid in owned}
                failures = [f for f in report["failures"] if f.get("persona_id") in owned]
                now = _utc_now()
                await writers[tenant].record_heartbeat("per_persona_ooda",
                    desired_state_query="persona owner eligible lifecycle records",
                    actual_state_query="OpenClaw daily-cycle jobs, terminal runs and persisted packets",
                    desired_state={"present": bool(owned), "source": "persona_owner", "checked_at": now},
                    downstream_actual_state={"status": "degraded" if failures else "ready",
                                             "source": "openclaw_cron", "checked_at": now},
                    evidence_refs=["services/persona/persona_cycle_controller.py"],
                    payload={"persona_cycle_health": health, "failures": failures})
        except Exception:
            log.exception("Persona daily-cycle reconciliation failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
