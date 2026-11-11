"""Real Governance ``evolution_execute`` approval for automatically approved research work.

The dispatch worker refuses to submit anything unless Governance holds a decided
``evolution_execute`` approval for exactly the stored proposal
(``dispatch_outbox.require_execution_approval``).  So the automated gate may not
invent an approval id: for a retrain / mutate / revalidate decision it

1. asks the Evolution owner to file the deterministic request through the
   existing ``execution-approval-request`` route (filed by the owner's own
   propose-only principal, so the gate is never the proposer);
2. reviews and decides that exact request in Governance with the
   ``automated_gate`` principal's own verified bearer token (low risk only,
   enforced by Governance's write-authority matrix, not here);
3. returns the real approval id, which the consumer binds before the Evolution
   review/approval.

No grant or issuer is added.  Every step is idempotent and resumable: the
request id is derived from the decision id and immutable content digest.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Callable, Mapping

from services.evolution.governed_persona_lifecycle import (
    GovernedLifecycleError, Transport, urllib_transport,
)

TokenProvider = Callable[[], str]


class ExecutionApprovalUnavailable(GovernedLifecycleError):
    pass


class GovernedExecutionApproval:
    def __init__(
        self,
        *,
        governance_url: str,
        gate_token: TokenProvider,
        gate_id: str,
        file_request: Callable[[str, str], Mapping[str, Any]],
        transport: Transport | None = None,
    ) -> None:
        self._gov = governance_url.rstrip("/")
        self._gate_token = gate_token
        self._gate_id = gate_id
        self._file_request = file_request  # (decision_id, tenant_id) -> Evolution route response
        self._send = transport or urllib_transport()

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None, key: str | None = None):
        headers = {"Authorization": f"Bearer {self._gate_token()}", "Accept": "application/json",
                   "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        status, payload = self._send(method, f"{self._gov}/api/governance/approvals{path}", headers, body)
        if status >= 400:
            raise GovernedLifecycleError(f"{method} approvals{path} -> {status}: {payload}")
        return payload if isinstance(payload, dict) else {}

    def ensure_approved(self, decision: Mapping[str, Any]) -> str:
        """Return the id of a decided-approved Governance execute approval for ``decision``."""
        decision_id = str(decision.get("decision_id") or "")
        tenant = str(decision.get("tenant_id") or "")
        filed = self._file_request(decision_id, tenant)
        approval_id = str(filed.get("approval_decision_id") or "")
        if not approval_id:
            raise ExecutionApprovalUnavailable(f"no execution approval id filed for {decision_id}")
        quoted = urllib.parse.quote(approval_id, safe="")
        current = self._call("GET", f"/{quoted}")
        state = str(current.get("decision_state") or "")
        version = int(current.get("version") or {"proposed": 1, "under_review": 2}.get(state, 1))
        gate = {"actor_role": "automated_gate", "actor_id": self._gate_id}
        if state == "proposed":
            current = self._call("POST", f"/{quoted}/review", {**gate, "expected_version": version},
                                 key=f"auto-review-{approval_id}")
            state, version = str(current.get("decision_state") or ""), int(current.get("version") or version + 1)
        if state == "under_review":
            current = self._call("POST", f"/{quoted}/decide", {
                **gate, "expected_version": version, "outcome": "approved",
                "expires_at": current.get("expires_at"),
                "rationale": f"automated discovery gate approves research execution for {decision_id}",
            }, key=f"auto-decide-{approval_id}")
            state = str(current.get("decision_state") or "")
        if state != "decided" or str(current.get("decision") or current.get("outcome") or "") != "approved":
            raise ExecutionApprovalUnavailable(f"governance approval {approval_id} not approved (state={state!r})")
        return approval_id
