"""Runtime adapters wiring AutoConsumer into the existing evolution scheduler worker.

HTTP ports over the evolution proposal API and the persona owner.  Everything
fails closed: an unreadable owner, a tenant mismatch, a non-paper lifecycle or a
target without exactly one active paper discovery Capital binding yields no
scope, so the decision stays with the operator.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from services.evolution.auto_consumer import AutoConsumer, ConsumerReport, JsonlEvidenceSink
from services.evolution.governed_execution_approval import GovernedExecutionApproval
from services.evolution.governed_persona_lifecycle import GovernedPersonaLifecycle, Transport, urllib_transport

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


DISCOVERY_BINDING_PREFIX = "pcb-discovery-"  # services/research/discovery_allocator.py: durable Capital binding ids


class DiscoveryBindingScope:
    """Owner-verified discovery scope resolved from the allocator's real records.

    The discovery allocator provisions a persona by creating a Capital binding
    ``pcb-discovery-*`` (paper only) whose ``metadata.strategy_spec_registry_id``
    names the admitted StrategySpec.  A decision target is in scope when it is
    that spec, or a Registry StrategyArtifact whose lineage names it, and exactly
    one active/pending tenant-owned discovery binding exists for it.  The persona
    is then read from the Persona owner (tenant, lifecycle).  No persona metadata
    marker is needed or trusted; any gap fails closed (``None``) or raises.
    """

    def __init__(
        self,
        *,
        capital_url: str,
        registry_url: str,
        persona_url: str,
        capital_token: Callable[[], str | None],
        registry_token: Callable[[], str | None],
        persona_token: Callable[[], str | None],
        transport: Transport | None = None,
    ) -> None:
        self._capital = capital_url.rstrip("/")
        self._registry = registry_url.rstrip("/")
        self._persona = persona_url.rstrip("/")
        self._tokens = {"capital": capital_token, "registry": registry_token, "persona": persona_token}
        self._send = transport or urllib_transport()

    def _get(self, owner: str, url: str, tenant: str, *, optional: bool = False) -> Any:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._tokens[owner]()}", "X-Tenant-Id": tenant}
        status, payload = self._send("GET", url, headers, None)
        if status == 404 and optional:
            return None
        if status >= 400:
            raise RuntimeError(f"{owner} owner unavailable: {status}")
        return payload

    def discovery_bindings(self, tenant: str) -> list[Mapping[str, Any]]:
        rows = self._get("capital", f"{self._capital}/api/bindings", tenant)
        out = []
        for b in rows if isinstance(rows, list) else []:
            meta = b.get("metadata") or {}
            if not str(b.get("binding_id") or "").startswith(DISCOVERY_BINDING_PREFIX):
                continue
            if str(b.get("tenant_id") or meta.get("tenant_id") or "") != tenant:
                continue
            if str(b.get("status") or "") not in {"pending", "active"}:
                continue
            out.append(b)
        return out

    def artifact_view(self, artifact_id: str, tenant: str) -> Mapping[str, Any] | None:
        import urllib.parse

        view = self._get("registry", f"{self._registry}/api/registry/strategy-artifacts/{urllib.parse.quote(artifact_id, safe='')}",
                         tenant, optional=True)
        return (view or {}).get("entry") if isinstance(view, dict) else None

    def resolve(self, decision: Mapping[str, Any]) -> Mapping[str, Any] | None:
        tenant = str(decision.get("tenant_id") or "")
        target = str(decision.get("target_id") or "")
        if not tenant or not target:
            return None
        entry = self.artifact_view(target, tenant)
        kind, spec_ids = "", {target}
        if entry is not None:
            if str(entry.get("owner_tenant") or (entry.get("metadata") or {}).get("tenant_id") or tenant) != tenant:
                return None
            artifact = (entry.get("metadata") or {}).get("strategy_artifact") or {}
            kind = str((artifact.get("strategy_logic") or {}).get("kind") or "")
            source = (artifact.get("lineage") or {}).get("source_strategy_spec_id")
            spec_ids = {str(source)} if source else set()
            persona_hint = str((entry.get("metadata") or {}).get("persona_id") or "")
        else:
            persona_hint = ""
        matches = [b for b in self.discovery_bindings(tenant)
                   if str((b.get("metadata") or {}).get("strategy_spec_registry_id") or "") in spec_ids]
        if persona_hint:
            matches = [b for b in matches if str(b.get("persona_id")) == persona_hint]
        decision_persona = str(decision.get("persona_id") or (decision.get("metadata") or {}).get("persona_id") or "")
        if decision_persona:
            matches = [b for b in matches if str(b.get("persona_id")) == decision_persona]
        if len(matches) != 1:
            return None
        persona_id = str(matches[0].get("persona_id") or "")
        persona = self._get("persona", f"{self._persona}/api/personas/{persona_id}", tenant)
        if not isinstance(persona, dict) or str(persona.get("tenant_id") or "") != tenant:
            return None
        state = str(persona.get("lifecycle_state") or "")
        return {
            "tenant_id": tenant, "persona_id": persona_id, "artifact_id": target,
            "binding_id": matches[0].get("binding_id"),
            "stage": "paper" if state in ("paper_owner", "frozen") else state,
            "strategy_kind": kind, "persona_lifecycle_state": state,
        }


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return (env or os.environ).get("EVOLUTION_AUTO_CONSUMER_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def build_consumer(env: Mapping[str, str] | None = None, transport: Transport | None = None) -> AutoConsumer:
    e = env or os.environ

    def need(name: str) -> str:
        value = e.get(name, "").strip()
        if not value:
            raise RuntimeError(f"{name} is required when EVOLUTION_AUTO_CONSUMER_ENABLED")
        return value

    def token(name: str) -> Callable[[], str]:
        return lambda: need(name)

    api = e.get("EVOLUTION_API_URL", "http://127.0.0.1:8093")
    tenant = (e.get("EVOLUTION_SCHEDULER_TENANT_ID") or e.get("EVOLUTION_DEFAULT_TENANT_ID") or "pantheon-default").strip()
    persona_url = need("AUTO_CONSUMER_PERSONA_URL")
    personas = GovernedPersonaLifecycle(
        governance_url=need("AUTO_CONSUMER_GOVERNANCE_URL"), persona_url=persona_url,
        proposer_token=token("AUTO_CONSUMER_PROPOSER_TOKEN"), proposer_id=need("AUTO_CONSUMER_PROPOSER_ID"),
        gate_token=token("AUTO_CONSUMER_GATE_TOKEN"), gate_id=need("AUTO_CONSUMER_GATE_ID"),
        persona_token=token("AUTO_CONSUMER_PERSONA_TOKEN"),
        evolution_decision_id=lambda: current_decision[0], transport=transport,
    )
    current_decision: list[str] = [""]
    scope = DiscoveryBindingScope(
        capital_url=need("AUTO_CONSUMER_CAPITAL_URL"), registry_url=need("AUTO_CONSUMER_REGISTRY_URL"),
        persona_url=persona_url, capital_token=lambda: e.get("AUTO_CONSUMER_CAPITAL_TOKEN"),
        registry_token=lambda: e.get("AUTO_CONSUMER_REGISTRY_TOKEN"),
        persona_token=lambda: e.get("AUTO_CONSUMER_PERSONA_TOKEN"), transport=transport,
    )
    decisions = HttpDecisionPort(api, lambda: e.get("EVOLUTION_AUTH_TOKEN") or None, transport, tenant)
    execution_approvals = GovernedExecutionApproval(
        governance_url=need("AUTO_CONSUMER_GOVERNANCE_URL"), gate_token=token("AUTO_CONSUMER_GATE_TOKEN"),
        gate_id=need("AUTO_CONSUMER_GATE_ID"), file_request=decisions.request_execution_approval,
        transport=transport,
    )
    consumer = AutoConsumer(
        decisions=decisions, execution_approvals=execution_approvals,
        scope=scope,
        evidence=JsonlEvidenceSink(e.get("AUTO_CONSUMER_EVIDENCE_PATH", "auto_consumer_evidence.jsonl")),
        personas=personas,
    )
    original = consumer.retire_persona

    def bound(decision, verdict, approval_id):
        current_decision[0] = str(decision.get("decision_id") or "")
        return original(decision, verdict, approval_id)

    consumer.retire_persona = bound  # type: ignore[method-assign]
    return consumer


def build_decay_monitor(env: Mapping[str, str] | None = None, transport: Transport | None = None):
    """Decay monitor over the same owners as the consumer plus the telemetry runtime summaries."""
    from services.evolution.decay_monitor import DecayMonitor, WindowLedger
    from services.evolution.threshold_sweep_worker import approved_baseline_value, default_fetch_summaries, load_baselines

    e = env or os.environ
    consumer = build_consumer(e, transport)
    tenant = (e.get("EVOLUTION_SCHEDULER_TENANT_ID") or e.get("EVOLUTION_DEFAULT_TENANT_ID") or "pantheon-default").strip()
    telemetry = e.get("EVOCHAIN_TELEMETRY_API_URL") or e.get("PANTHEON_TELEMETRY_URL", "http://telemetry:8083")
    baselines = load_baselines()
    return DecayMonitor(
        tenant_id=tenant, scope=consumer.scope,
        fetch_summaries=lambda: default_fetch_summaries(
            telemetry, timeout=20.0, tenant_id=tenant, service_token=e.get("AUTO_CONSUMER_TELEMETRY_TOKEN")),
        baseline=lambda artifact, key: approved_baseline_value(baselines, artifact, key),
        ledger=WindowLedger(e.get("AUTO_CONSUMER_DECAY_LEDGER_PATH", "paper_decay_windows.jsonl")),
        propose=consumer.decisions.propose, evidence=consumer.evidence,
    ), consumer


def run_consumer_tick(env: Mapping[str, str] | None = None, transport: Transport | None = None) -> dict[str, Any]:
    """One scheduler step: detect paper decay (proposes retires), then gate/approve/retire."""
    monitor, consumer = build_decay_monitor(env, transport)
    decay: dict[str, Any]
    try:
        d = monitor.tick()
        decay = {"observed": d.observed, "proposed": d.proposed, "existing": d.existing, "errors": d.errors}
    except Exception as exc:  # telemetry/capital outage must not stop the consumer tick; retried next tick
        decay = {"errors": {"decay_monitor": f"{type(exc).__name__}: {exc}"}}
    report: ConsumerReport = consumer.tick()
    return {"approved": report.approved, "retired": report.retired,
            "left_for_operator": report.left_for_operator, "errors": report.errors, "decay": decay}
