"""Runtime adapters wiring AutoConsumer into the existing evolution scheduler worker.

HTTP ports over the evolution proposal API and the persona owner.  Everything
fails closed: an unreadable owner, a tenant mismatch, a non-paper lifecycle or a
persona that does not name the artifact as its discovery artifact yields no
scope, so the decision stays with the operator.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from services.evolution.auto_consumer import AutoConsumer, ConsumerReport, JsonlEvidenceSink
from services.evolution.governed_persona_lifecycle import GovernedPersonaLifecycle, Transport, urllib_transport

DISCOVERY_ARTIFACT_KEY = "discovery_artifact_id"  # persona.metadata key naming its discovery artifact
DISCOVERY_KIND_KEY = "discovery_strategy_kind"


class HttpDecisionPort:
    def __init__(self, base_url: str, token: Callable[[], str | None], transport: Transport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
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
        rows = self._call("GET", f"/api/evolution/proposals?state={state}", None)
        return rows if isinstance(rows, list) else list((rows or {}).get("decisions", []))

    def review(self, decision_id: str, body: Mapping[str, Any]):
        return self._call("POST", f"/api/evolution/proposals/{decision_id}/review", body, body.get("tenant_id"))

    def approve(self, decision_id: str, body: Mapping[str, Any]):
        return self._call("POST", f"/api/evolution/proposals/{decision_id}/approve", body, body.get("tenant_id"))


class PersonaOwnerScope:
    """Owner-verified discovery scope: tenant-owned persona in ``paper_owner`` naming the target artifact."""

    def __init__(self, persona_url: str, token: Callable[[], str | None], transport: Transport | None = None) -> None:
        self._url = persona_url.rstrip("/")
        self._token = token
        self._send = transport or urllib_transport()

    def resolve(self, decision: Mapping[str, Any]) -> Mapping[str, Any] | None:
        tenant = str(decision.get("tenant_id") or "")
        artifact = str(decision.get("target_id") or "")
        token = self._token()
        status, rows = self._send("GET", f"{self._url}/api/personas", {"Authorization": f"Bearer {token}", "Accept": "application/json"}, None)
        if status >= 400:
            raise RuntimeError(f"persona owner unavailable: {status}")
        matches = [
            p for p in (rows if isinstance(rows, list) else [])
            if p.get("tenant_id") == tenant and (p.get("metadata") or {}).get(DISCOVERY_ARTIFACT_KEY) == artifact
        ]
        if len(matches) != 1:
            return None
        persona = matches[0]
        state = str(persona.get("lifecycle_state") or "")
        return {
            "tenant_id": tenant, "persona_id": persona["persona_id"], "artifact_id": artifact,
            "stage": "paper" if state in ("paper_owner", "frozen") else state,
            "strategy_kind": (persona.get("metadata") or {}).get(DISCOVERY_KIND_KEY, ""),
            "persona_lifecycle_state": state,
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
    persona_url = need("AUTO_CONSUMER_PERSONA_URL")
    personas = GovernedPersonaLifecycle(
        governance_url=need("AUTO_CONSUMER_GOVERNANCE_URL"), persona_url=persona_url,
        proposer_token=token("AUTO_CONSUMER_PROPOSER_TOKEN"), proposer_id=need("AUTO_CONSUMER_PROPOSER_ID"),
        gate_token=token("AUTO_CONSUMER_GATE_TOKEN"), gate_id=need("AUTO_CONSUMER_GATE_ID"),
        persona_token=token("AUTO_CONSUMER_PERSONA_TOKEN"),
        evolution_decision_id=lambda: current_decision[0], transport=transport,
    )
    current_decision: list[str] = [""]
    consumer = AutoConsumer(
        decisions=HttpDecisionPort(api, lambda: e.get("EVOLUTION_AUTH_TOKEN") or None, transport),
        scope=PersonaOwnerScope(persona_url, lambda: e.get("AUTO_CONSUMER_PERSONA_TOKEN"), transport),
        evidence=JsonlEvidenceSink(e.get("AUTO_CONSUMER_EVIDENCE_PATH", "auto_consumer_evidence.jsonl")),
        personas=personas,
    )
    original = consumer.retire_persona

    def bound(decision, verdict, approval_id):
        current_decision[0] = str(decision.get("decision_id") or "")
        return original(decision, verdict, approval_id)

    consumer.retire_persona = bound  # type: ignore[method-assign]
    return consumer


def run_consumer_tick(env: Mapping[str, str] | None = None, transport: Transport | None = None) -> dict[str, Any]:
    report: ConsumerReport = build_consumer(env, transport).tick()
    return {"approved": report.approved, "retired": report.retired,
            "left_for_operator": report.left_for_operator, "errors": report.errors}
