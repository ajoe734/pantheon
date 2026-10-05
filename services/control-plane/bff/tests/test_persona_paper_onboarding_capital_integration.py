"""Persona paper onboarding against the real isolated Capital owner.

The provisioning coordinator drives the real Capital service for the pool and binding (create AND
activate) with no Capital approval decision and no invented risk policy; Registry/Governance/Deployment
stay on their own fake owners.  Afterwards the paper binding is suspended, reactivated and reloaded
through the BFF forwarding path.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError

from services.control_plane.bff.persona_provisioning_coordinator import deterministic_provisioning_ids
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _coordinator,
    _record_and_store,
    _schedule_receipt,
)
from services.control_plane.bff.tests.rebalance_authority_test_support import CapitalBffAuthorityHarness


class _CapitalOwnerBackedTransport:
    """Capital calls hit the real owner service; every other owner keeps its fixture fake."""

    def __init__(self, capital_client: Any, others: FakeOwnerTransport) -> None:
        self._capital = capital_client
        self._others = others
        self.capital_posts: list[tuple[str, Mapping[str, Any]]] = []

    def get(self, owner: str, path: str) -> Any:
        if owner != "capital":
            return self._others.get(owner, path)
        response = self._capital.get(path)
        return None if response.status_code == 404 else self._body(response, path)

    def post(self, owner: str, path: str, payload: Mapping[str, Any]) -> Any:
        if owner != "capital":
            return self._others.post(owner, path, payload)
        self.capital_posts.append((path, dict(payload)))
        return self._body(self._capital.post(path, json=dict(payload)), path)

    def patch(self, owner: str, path: str, payload: Mapping[str, Any]) -> Any:
        if owner != "capital":
            return self._others.patch(owner, path, payload)
        return self._body(self._capital.patch(path, json=dict(payload)), path)

    @staticmethod
    def _body(response: Any, path: str) -> Any:
        if response.status_code >= 400:
            raise HTTPError(path, response.status_code, response.reason_phrase, response.headers, BytesIO(response.content))
        return response.json()


def test_paper_onboarding_creates_and_activates_pool_and_binding_without_approval_or_policy(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        store, record = _record_and_store()
        ids = deterministic_provisioning_ids(record)
        transport = _CapitalOwnerBackedTransport(harness.capital_client, FakeOwnerTransport())

        result = _coordinator(store, transport, _schedule_receipt).coordinate(record)

        assert result.state == "provisioning" and result.current_step == "schedule_registered", result.error
        pool = harness.capital_client.get(f"/api/capital-pools/{ids.capital_pool_id}").json()
        assert pool["status"] == "active"
        assert pool["risk_policy_ref"] is None  # no invented policy
        assert pool["metadata"]["execution_context"] == "paper"
        binding = harness.capital_client.get(f"/api/bindings/{ids.persona_capital_binding_id}").json()
        assert (binding["role"], binding["allowed_deployment_scope"], binding["status"]) == ("paper_owner", "paper", "active")
        assert binding["approval_decision_id"] is None  # the Registry decision is never Capital authority
        activation = [payload for path, payload in transport.capital_posts if path.endswith("/activate")]
        assert activation and all("approval_decision_id" not in payload for payload in activation)

        # Suspend / reactivate / reload through the BFF forwarding path.
        key = "paper-lifecycle"
        target = {"type": "PersonaCapitalBinding", "id": ids.persona_capital_binding_id}
        base = {"entity_type": "binding", "entity_id": ids.persona_capital_binding_id}
        suspended = harness.run_command("PersonaAction", target, {**base, "action_id": "update_status", "status": "suspended"}, key=f"{key}-suspend")
        assert suspended["status"] == "executed", suspended["error"]
        assert harness.capital_client.get(f"/api/bindings/{ids.persona_capital_binding_id}").json()["status"] == "suspended"
        reactivated = harness.run_command("PersonaAction", target, {**base, "action_id": "activate"}, key=f"{key}-activate")
        assert reactivated["status"] == "executed", reactivated["error"]

        harness.restart()
        reloaded = harness.capital_client.get(f"/api/bindings/{ids.persona_capital_binding_id}").json()
        assert reloaded["status"] == "active" and reloaded["approval_decision_id"] is None
        assert harness.capital_client.get(f"/api/capital-pools/{ids.capital_pool_id}").json()["status"] == "active"

        assert len(harness.capital_client.get("/api/capital-pools").json()) == 2  # harness pool + onboarded paper pool
        assert len(harness.capital_client.get("/api/bindings").json()) == 2
