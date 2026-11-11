"""PersonaLifecyclePort backed by the existing Governance approval API.

Each exact ``(persona, from_state, to_state)`` retire step is proposed, reviewed
and decided through ``/api/governance/approvals`` as ``persona_lifecycle_transition``
(low risk, ``automated_gate``), then the persona owner advances the lifecycle
citing that real Governance decision id, which the owner re-verifies itself.

Nothing here issues credentials or trusts submitted actor labels: the proposer
and the gate each present their own verified bearer token (distinct subjects),
and Governance derives tenant/actor from the token.  Replays are safe: every
command carries a deterministic Idempotency-Key and a decision id derived from
the evolution decision plus the exact transition.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

Transport = Callable[[str, str, Mapping[str, str], "Mapping[str, Any] | None"], "tuple[int, Any]"]
TokenProvider = Callable[[], str]

DEFAULT_EXPIRES_AT = "2099-01-01T00:00:00Z"


class GovernedLifecycleError(RuntimeError):
    pass


def urllib_transport(timeout_seconds: float = 15.0) -> Transport:
    def send(method: str, url: str, headers: Mapping[str, str], body: Mapping[str, Any] | None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"detail": raw[:200]}

    return send


def _digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(list(parts)).encode()).hexdigest()[:24]


def transition_approval_id(evolution_decision_id: str, persona_id: str, source: str, target: str) -> str:
    """Deterministic Governance decision id for one exact retire step of one evolution decision."""
    return f"apv-evo-{_digest(evolution_decision_id, persona_id, source, target)}"


class GovernedPersonaLifecycle:
    def __init__(
        self,
        *,
        governance_url: str,
        persona_url: str,
        proposer_token: TokenProvider,
        proposer_id: str,
        gate_token: TokenProvider,
        gate_id: str,
        persona_token: TokenProvider,
        evolution_decision_id: Callable[[], str],
        transport: Transport | None = None,
    ) -> None:
        if proposer_id == gate_id:
            raise ValueError("proposer and gate identities must be distinct")
        self._gov = governance_url.rstrip("/")
        self._persona = persona_url.rstrip("/")
        self._proposer_token = proposer_token
        self._proposer_id = proposer_id
        self._gate_token = gate_token
        self._gate_id = gate_id
        self._persona_token = persona_token
        self._decision_id = evolution_decision_id
        self._send = transport or urllib_transport()

    def _call(self, method: str, url: str, token: str, body=None, idempotency: str | None = None):
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        status, payload = self._send(method, url, headers, body)
        if status >= 400:
            raise GovernedLifecycleError(f"{method} {url.split('/api/', 1)[-1]} -> {status}: {payload}")
        return payload if isinstance(payload, dict) else {}

    def get_state(self, persona_id: str, tenant_id: str) -> str:
        persona = self._call("GET", f"{self._persona}/api/personas/{persona_id}", self._persona_token())
        if str(persona.get("tenant_id") or "") != tenant_id:
            raise GovernedLifecycleError(f"persona {persona_id} is not owned by tenant {tenant_id}")
        return str(persona.get("lifecycle_state") or "")

    def advance(self, persona_id: str, tenant_id: str, target_state: str, governance_decision_id: str,
                expected_source: str | None = None) -> str:
        source = self.get_state(persona_id, tenant_id)
        if source == target_state:
            return source  # caller must still prove this decision made the transition (verify_freeze)
        if expected_source is not None and source != expected_source:
            raise GovernedLifecycleError(
                f"persona {persona_id} is {source!r}, expected {expected_source!r}: refusing to mint an approval for a changed source")
        key = _digest(self._decision_id(), persona_id, source, target_state)
        approval_id = f"apv-evo-{key}"
        base = f"{self._gov}/api/governance/approvals"
        existing = self._existing(approval_id)
        if existing is None:
            self._call("POST", base, self._proposer_token(), {
                "decision_id": approval_id, "expected_version": 0, "tenant_id": tenant_id,
                "owner_user_id": self._proposer_id, "target_type": "persona_lifecycle_transition",
                "target_id": persona_id, "target_version": key, "risk_level": "low", "persona_id": persona_id,
                "expires_at": DEFAULT_EXPIRES_AT,
                "subject": {"persona_id": persona_id, "from_state": source, "to_state": target_state},
            }, idempotency=f"propose-{key}")
            existing = self._existing(approval_id) or {}
        state = str(existing.get("decision_state") or "")
        version = int(existing.get("version") or {"proposed": 1, "under_review": 2}.get(state, 1))
        gate = {"actor_role": "automated_gate", "actor_id": self._gate_id}
        if state == "proposed":
            existing = self._call("POST", f"{base}/{approval_id}/review", self._gate_token(),
                                  {**gate, "expected_version": version}, idempotency=f"review-{key}")
            state, version = str(existing.get("decision_state") or ""), int(existing.get("version") or version + 1)
        if state == "under_review":
            existing = self._call("POST", f"{base}/{approval_id}/decide", self._gate_token(), {
                **gate, "expected_version": version, "outcome": "approved", "expires_at": DEFAULT_EXPIRES_AT,
                "rationale": f"automated discovery paper retire for evolution decision {self._decision_id()}",
            }, idempotency=f"decide-{key}")
            state = str(existing.get("decision_state") or "")
        if state != "decided" or str(existing.get("decision") or existing.get("outcome") or "approved") != "approved":
            raise GovernedLifecycleError(f"governance decision {approval_id} not approved (state={state!r})")
        advanced = self._call("PATCH", f"{self._persona}/api/personas/{persona_id}/lifecycle", self._persona_token(), {
            "actor_id": self._gate_id, "target_state": target_state, "governance_decision_id": approval_id,
        })
        return str(advanced.get("lifecycle_state") or "")

    def verify_freeze(self, evolution_decision_id: str, persona_id: str, tenant_id: str) -> bool:
        """True only if this evolution decision's own approved paper_owner->frozen step froze the persona.

        Checks the Persona owner binding (last lifecycle governance id) and the
        Governance record (decided/approved, exact persona and transition).
        """
        approval_id = transition_approval_id(evolution_decision_id, persona_id, "paper_owner", "frozen")
        persona = self._call("GET", f"{self._persona}/api/personas/{persona_id}", self._persona_token())
        meta = persona.get("metadata") or {}
        if (str(persona.get("tenant_id") or "") != tenant_id or str(persona.get("lifecycle_state") or "") != "frozen"
                or str(meta.get("last_lifecycle_governance_decision_id") or "") != approval_id):
            return False
        record = self._existing(approval_id) or {}
        subject = record.get("subject") or {}
        return (str(record.get("decision_state") or "") == "decided"
                and str(record.get("decision") or record.get("outcome") or "approved") == "approved"
                and str(record.get("target_id") or persona_id) == persona_id
                and str(subject.get("persona_id") or "") == persona_id
                and str(subject.get("from_state") or "") == "paper_owner"
                and str(subject.get("to_state") or "") == "frozen")

    def _existing(self, approval_id: str) -> dict[str, Any] | None:
        try:
            return self._call("GET", f"{self._gov}/api/governance/approvals/{approval_id}", self._gate_token())
        except GovernedLifecycleError as exc:
            if " 404" in str(exc) or "-> 404" in str(exc):
                return None
            raise
