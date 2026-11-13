"""Runtime adapters wiring AutoConsumer into the existing evolution scheduler worker.

HTTP ports over the existing proposal, pool, registry and publishing owners.
Everything fails closed: missing member books, mismatched identity or a
non-paper persona cannot authorize an automatic pool change.
"""

from __future__ import annotations

import os
import hashlib
import json
from typing import Any, Callable, Mapping

from services.evolution.auto_consumer import AutoConsumer, ConsumerReport, JsonlEvidenceSink
from services.evolution.governed_execution_approval import GovernedExecutionApproval
from services.evolution.governed_persona_lifecycle import Transport, urllib_transport

class HttpDecisionPort:
    def __init__(self, base_url: str, token: Callable[[], str | None], transport: Transport | None = None,
                 tenant: str | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._tenant = (tenant or "").strip() or None
        self._send = transport or urllib_transport()

    def _headers(self, tenant: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if tenant:
            headers["X-Tenant-Id"] = tenant
        return headers

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None, tenant: str | None = None) -> Any:
        status, payload = self._send(method, f"{self._base}{path}", self._headers(tenant), body)
        if status >= 400:
            raise RuntimeError(f"{method} {path} -> {status}")
        return payload

    def list_by_state(self, state: str):
        if not self._tenant:
            raise RuntimeError("decision reads need a configured tenant (token-mode evolution requires X-Tenant-Id)")
        rows = self._call("GET", f"/api/evolution/proposals?decision_state={state}", None, self._tenant)
        return rows if isinstance(rows, list) else list((rows or {}).get("decisions", []))

    def request_execution_approval(self, decision_id: str, tenant_id: str):
        """Owner files the Governance ``evolution_execute`` request (existing route)."""
        return self._call("POST", f"/api/evolution/proposals/{decision_id}/execution-approval-request",
                          {"tenant_id": tenant_id}, tenant_id)

    def propose(self, body: Mapping[str, Any]):
        return self._call("POST", "/api/evolution/proposals", body, body.get("tenant_id"))

    def review(self, decision_id: str, body: Mapping[str, Any]):
        return self._call("POST", f"/api/evolution/proposals/{decision_id}/review", body, body.get("tenant_id"))

    def approve(self, decision_id: str, body: Mapping[str, Any]):
        return self._call("POST", f"/api/evolution/proposals/{decision_id}/approve", body, body.get("tenant_id"))


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get("EVOLUTION_AUTO_CONSUMER_ENABLED", "").strip().lower() in {"1", "true", "yes"}


class PoolOwner:
    """Authenticated reads/writes to the existing product owners. No lifecycle port."""

    def __init__(self, env, transport=None):
        self.env = env
        self.send = transport or urllib_transport()
        self.tenant = (env.get("EVOLUTION_SCHEDULER_TENANT_ID") or env.get("EVOLUTION_DEFAULT_TENANT_ID") or "pantheon-default").strip()

    def call(self, owner, method, path, body=None, *, optional=False):
        base = self.env.get(f"AUTO_CONSUMER_{owner}_URL", "").rstrip("/")
        token = self.env.get(f"AUTO_CONSUMER_{owner}_TOKEN", "")
        if owner == "GOVERNANCE":
            gate = method == "POST" and (path.endswith("/review") or path.endswith("/decide"))
            token = self.env.get("AUTO_CONSUMER_GATE_TOKEN" if gate else "AUTO_CONSUMER_PROPOSER_TOKEN", "")
        if not base or not token:
            raise RuntimeError(f"AUTO_CONSUMER_{owner}_URL and TOKEN are required")
        status, payload = self.send(method, base + path,
            {"Authorization": f"Bearer {token}", "X-Tenant-Id": self.tenant,
             "Accept": "application/json", "Content-Type": "application/json"}, body)
        if optional and status == 404:
            return None
        if status >= 400:
            raise RuntimeError(f"{owner} {method} {path} -> {status}")
        return payload

    def pool(self, pid):
        from urllib.parse import quote
        row = self.call("PERSONA", "GET", f"/api/personas/{quote(pid, safe='')}/strategy-pool")
        if row.get("tenant_id") != self.tenant or row.get("persona_id") != pid:
            raise RuntimeError("pool owner identity mismatch")
        return row

    def pools(self):
        rows = self.call("PERSONA", "GET", "/api/personas?lifecycle_state=paper_owner")
        for row in rows:
            if row.get("tenant_id") != self.tenant or row.get("lifecycle_state") != "paper_owner":
                continue
            from urllib.parse import quote
            pool = self.call("PERSONA", "GET", f"/api/personas/{quote(row['persona_id'], safe='')}/strategy-pool", optional=True)
            if pool is not None:
                if pool.get("tenant_id") != self.tenant or pool.get("persona_id") != row["persona_id"]:
                    raise RuntimeError("pool owner identity mismatch")
                yield pool

    def entry(self, ref):
        from urllib.parse import quote
        view = self.call("REGISTRY", "GET", "/api/registry/strategy-artifacts/" + quote(ref, safe=""))
        entry = view.get("entry") or {}
        if entry.get("registry_id") != ref or entry.get("owner_tenant") != self.tenant:
            raise RuntimeError("registry owner identity mismatch")
        return entry

    def resolve(self, decision):
        from urllib.parse import quote
        if decision.get("tenant_id") != self.tenant:
            return None
        ctx = (decision.get("metadata") or {}).get("pool_member") or {}
        pid = ctx.get("persona_id") or decision.get("persona_id")
        if not pid:
            return None
        persona = self.call("PERSONA", "GET", f"/api/personas/{quote(pid, safe='')}")
        if persona.get("tenant_id") != self.tenant or persona.get("lifecycle_state") != "paper_owner":
            return None
        pool = self.pool(pid)
        member = next((m for m in pool["members"] if m["artifact_ref"] == decision["target_id"]), None)
        if member is None or (member["stage"] == "removed" and
                             member.get("removal_reason") != "evolution:" + decision["decision_id"]):
            return None
        if ctx and any(ctx.get(k) != member.get(k) for k in ("artifact_ref", "checksum", "joined_at")):
            return None
        entry = self.entry(member["artifact_ref"])
        if entry.get("artifact_state") != "approved" or entry.get("checksum") != member["checksum"] or entry.get("version") != decision.get("target_version"):
            return None
        return {"tenant_id": self.tenant, "persona_id": pid, "artifact_id": member["artifact_ref"],
                "stage": "paper", "strategy_kind": entry["metadata"]["strategy_artifact"]["strategy_logic"]["kind"],
                "pool_member": member, "pool_version": pool["pool_version"]}

    def books(self, pool):
        payload = self.call("TELEMETRY", "GET", "/api/telemetry/runtime-summaries")
        rows = payload if isinstance(payload, list) else payload.get("summaries", [])
        bindings = self.call("CAPITAL", "GET", "/api/bindings")
        bindings = [b for b in bindings if b.get("persona_id") == pool["persona_id"]
                    and b.get("tenant_id") == self.tenant and b.get("status") == "active"]
        if len(bindings) != 1:
            raise RuntimeError("exactly one active persona capital binding is required")
        rows = [r for r in rows if r.get("persona_capital_binding_id") == bindings[0]["binding_id"]
                and r.get("deployment_stage") == "paper" and r.get("tenant_id") == self.tenant]
        if len(rows) != 1:
            raise RuntimeError("exactly one current paper runtime summary is required")
        books = rows[0].get("member_virtual_books") or []
        if len({b.get("member_id") or b["artifact_id"] for b in books}) != len(books):
            raise RuntimeError("ambiguous member virtual books")
        return {b.get("member_id") or b["artifact_id"]: {**b, **b.get("net_of_costs", {})} for b in books}

    def family_state(self, family, bar_frequency=None):
        from urllib.parse import quote
        path = "/api/research/evolution/families/" + quote(family, safe="")
        if bar_frequency:
            path += "?bar_frequency=" + quote(str(bar_frequency), safe="")
        return self.call("RESEARCH", "GET", path)

    def publish(self, pool):
        from urllib.parse import quote
        result = self.call("BFF", "POST", f"/bff/personas/{quote(pool['persona_id'], safe='')}/strategy-pool/publish",
                           {"pool_version": pool["pool_version"], "stage": "paper"})
        receipt = result.get("data") or result
        if receipt.get("status") != "published":
            raise RuntimeError("pool envelope publication is not confirmed")
        return receipt

    def remove_member(self, pool, member, *, reason):
        from datetime import datetime, timezone
        from urllib.parse import quote
        current = self.pool(pool["persona_id"])
        members = [dict(m) for m in current["members"]]
        found = next((m for m in members if m["artifact_ref"] == member["artifact_ref"]), None)
        if found is None or found["checksum"] != member["checksum"] or found["joined_at"] != member["joined_at"]:
            raise RuntimeError("pool member identity changed before removal")
        already_removed = found["stage"] == "removed"
        if already_removed and found.get("removal_reason") != reason:
            raise RuntimeError("member was removed by another decision")
        if not already_removed:
            found.update(stage="removed", risk_weight=0, removal_reason=reason,
                         removed_at=datetime.now(timezone.utc).isoformat())
            current = self.call("PERSONA", "POST", f"/api/personas/{quote(pool['persona_id'], safe='')}/strategy-pool/versions",
                {"expected_version": current["pool_version"], "members": members,
                 "change_reason": reason})
        receipt = self.publish(current)  # retry publication even when append already landed
        return {**receipt, "already_removed": already_removed, "removed": member["artifact_ref"]}

    def compete(self, pool, child, books):
        from urllib.parse import quote
        result = self.call("PERSONA", "POST", f"/api/personas/{quote(pool['persona_id'], safe='')}/strategy-pool/decisions",
            {"expected_version": pool["pool_version"], "execution_context": "paper", "action": "evolution_compete",
             "candidate": {"artifact_ref": child["artifact_ref"], "checksum": child["checksum"],
                           "parent_ref": child["parent_ref"]}, "books": books})
        if (result.get("decision") or {}).get("status") != "applied":
            raise RuntimeError("pool competition refused: " + str(result.get("decision")))
        return self.publish(result)

    def admit_child(self, ctx, child_id, decision_id):
        """Approve the exact gate-passed child, then append a zero-weight trial."""
        from urllib.parse import quote
        from datetime import datetime, timedelta, timezone
        from services.evolution.approved_baselines import resolve_member_baseline
        entry = self.entry(child_id)
        if entry.get("replication_success") is not True or not entry.get("replication_proof"):
            raise RuntimeError("child has no registry-owned gate proof")
        approval_id = "pool-child-" + hashlib.sha256((self.tenant + decision_id + child_id).encode()).hexdigest()[:24]
        digest = entry["checksum"]
        if entry["artifact_state"] == "candidate":
            path = "/api/governance/approvals/" + approval_id
            approval = self.call("GOVERNANCE", "GET", path, optional=True)
            if approval is None:
                approval = self.call("GOVERNANCE", "POST", "/api/governance/approvals", {
                    "decision_id": approval_id, "expected_version": 0, "tenant_id": self.tenant,
                    "target_type": "registry_entry", "target_id": child_id, "target_version": entry["version"],
                    "risk_level": "low", "persona_id": ctx["persona_id"],
                    "owner_user_id": self.env.get("AUTO_CONSUMER_PROPOSER_ID"),
                    "proposal_id": decision_id, "proposal_revision": 1,
                    "proposal_content_digest": hashlib.sha256(json.dumps(ctx, sort_keys=True).encode()).hexdigest(),
                    "validation_result_digest": digest, "candidate_digest": digest,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()})
            if any(approval.get(k) != v for k, v in {"target_id": child_id, "target_version": entry["version"],
                    "tenant_id": self.tenant, "candidate_digest": digest, "risk_level": "low"}.items()):
                raise RuntimeError("child approval identity mismatch")
            for step in ("review", "decide"):
                if approval.get("decision_state") == "decided":
                    break
                if step == "review" and approval.get("decision_state") == "under_review":
                    continue
                body = {"actor_role": "automated_gate", "actor_id": self.env.get("AUTO_CONSUMER_GATE_ID"),
                        "expected_version": approval["version"]}
                if step == "decide":
                    body.update(outcome="approved", candidate_digest=digest, expires_at=approval["expires_at"],
                                rationale="Gate-passed child, paper virtual-only trial",
                                evidence_refs=[{"ref_type": "registry_entry", "ref_id": child_id}])
                approval = self.call("GOVERNANCE", "POST", path + "/" + step, body)
            if approval.get("decision") != "approved" or approval.get("decision_state") != "decided":
                raise RuntimeError("child approval is not decided approved")
            self.call("REGISTRY", "POST", "/api/registry/strategy-artifacts/" + quote(child_id, safe="") + "/advance", {
                "target_state": "approved", "approval_decision_id": approval_id,
                "expected_artifact_state": "candidate", "expected_version": entry["version"],
                "expected_updated_at": entry.get("updated_at"), "command_key": approval_id})
            entry = self.entry(child_id)
        baseline = resolve_member_baseline(tenant_id=self.tenant, persona_id=ctx["persona_id"],
            artifact_ref=child_id, checksum=digest, entry=entry)
        if baseline["status"] != "approved":
            raise RuntimeError("child approved baseline unavailable: " + baseline["reason"])
        pool = self.pool(ctx["persona_id"])
        existing = next((m for m in pool["members"] if m["artifact_ref"] == child_id), None)
        if existing is None:
            result = self.call("PERSONA", "POST", f"/api/personas/{quote(ctx['persona_id'], safe='')}/strategy-pool/decisions", {
                "expected_version": pool["pool_version"], "execution_context": "paper", "action": "evolution_trial",
                "candidate": {"artifact_ref": child_id, "checksum": digest, "parent_ref": ctx["artifact_ref"]}})
            if (result.get("decision") or {}).get("status") != "applied":
                raise RuntimeError("child trial admission refused: " + str(result.get("decision")))
            pool = result
        elif existing["checksum"] != digest or existing.get("parent_ref") != ctx["artifact_ref"]:
            raise RuntimeError("child trial identity mismatch")
        return self.publish(pool)

    def retire(self, decision, scope):
        return self.remove_member(self.pool(scope["persona_id"]), scope["pool_member"],
                                  reason="evolution:" + decision["decision_id"])


def build_consumer(env: Mapping[str, str] | None = None, transport: Transport | None = None) -> AutoConsumer:
    e = os.environ if env is None else env
    owner = PoolOwner(e, transport)
    decisions = HttpDecisionPort(e.get("EVOLUTION_API_URL", "http://127.0.0.1:8093"),
        lambda: e.get("EVOLUTION_AUTH_TOKEN") or None, transport, owner.tenant)
    approvals = GovernedExecutionApproval(
        governance_url=e.get("AUTO_CONSUMER_GOVERNANCE_URL", ""),
        gate_token=lambda: e.get("AUTO_CONSUMER_GATE_TOKEN", ""),
        gate_id=e.get("AUTO_CONSUMER_GATE_ID", ""), file_request=decisions.request_execution_approval,
        transport=transport)
    return AutoConsumer(decisions=decisions, scope=owner, pool_actions=owner,
        execution_approvals=approvals,
        evidence=JsonlEvidenceSink(e.get("AUTO_CONSUMER_EVIDENCE_PATH", "auto_consumer_evidence.jsonl")))


def run_consumer_tick(env: Mapping[str, str] | None = None, transport: Transport | None = None) -> dict[str, Any]:
    """The existing scheduler invokes this entry point; proposals precede approval."""
    from dataclasses import asdict
    from services.evolution.pool_evolution_scheduler import PoolEvolutionScheduler
    from services.evolution.decay_monitor import WindowLedger
    e = os.environ if env is None else env
    consumer = build_consumer(e, transport)
    try:
        schedule = asdict(PoolEvolutionScheduler(owner=consumer.scope, decisions=consumer.decisions,
            evidence=consumer.evidence,
            ledger=WindowLedger(e.get("AUTO_CONSUMER_DECAY_LEDGER_PATH", "paper_decay_windows.jsonl"))).tick())
    except Exception as exc:
        schedule = {"errors": {"pool_scheduler": f"{type(exc).__name__}: {exc}"}}
    report = consumer.tick()
    return {**asdict(report), "schedule": schedule}
