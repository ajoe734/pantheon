"""Mounted proofs that the single CapitalOwnerWriter reaches the isolated real Capital owner.

Pool/binding/status/rebalance/containment entries run through stored-command processing (or the mounted
REST route) against the real owner service with the caller's own JWT; the owner's CapitalGuard alone
decides whether a Capital approval is needed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from services.capital import capital_guard
from services.control_plane.bff import command_executor
from services.control_plane.bff.command_adapters import service as command_service
from services.control_plane.bff.models import CommandType, ObjectType, TargetObject, utc_now
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    HEADERS,
    CapitalBffAuthorityHarness,
)
from services.governance.approval_authority import ApprovalInvalid

VIEWER = {"Authorization": "Bearer viewer-1:viewer"}
BINDING = "PersonaCapitalBinding"


def _paper_pool(harness: CapitalBffAuthorityHarness, pool_id: str = "pool-paper") -> Dict[str, Any]:
    response = harness.client.post(
        "/bff/capital-pools",
        json={"pool_id": pool_id, "name": "Paper pool", "owner_id": "tenant-test", "owner_type": "org",
              "metadata": {"execution_context": "paper"}},
        headers={**HEADERS, "Idempotency-Key": f"create-{pool_id}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _binding(binding_id: str, pool_id: str, *, role: str, scope: str, persona_id: str = "p-paper", **extra: Any) -> Dict[str, Any]:
    return command_executor.create_capital_binding(
        {"binding_id": binding_id, "persona_id": persona_id, "capital_pool_id": pool_id, "role": role,
         "allowed_deployment_scope": scope, "actor_id": "op-2", "actor_role": "operator", **extra},
        auth_token=HEADERS["Authorization"],
    )


def _binding_command(harness: CapitalBffAuthorityHarness, binding_id: str, action: str, key: str, **params: Any) -> Dict[str, Any]:
    return harness.run_command(
        "PersonaAction",
        {"type": BINDING, "id": binding_id},
        {"action_id": action, "entity_type": "binding", "entity_id": binding_id, **params},
        key=key,
    )


def _owner_binding(harness: CapitalBffAuthorityHarness, binding_id: str) -> Dict[str, Any]:
    return harness.capital_client.get(f"/api/bindings/{binding_id}").json()


def test_paper_onboarding_creates_activates_suspends_reactivates_and_reloads_without_approval(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        pool = _paper_pool(harness)  # no risk_policy_ref, no approval decision
        assert pool["status"] == "active" and pool["risk_policy_ref"] is None
        assert _binding("b-paper", "pool-paper", role="paper_owner", scope="paper")["status"] == "pending"

        activated = _binding_command(harness, "b-paper", "activate", "paper-activate")
        assert activated["status"] == "executed", activated
        assert activated["result"]["authoritative_readback"]["status"] == "active"
        owner = _owner_binding(harness, "b-paper")
        assert owner["status"] == "active" and owner["approval_decision_id"] is None

        suspended = _binding_command(harness, "b-paper", "update_status", "paper-suspend", status="suspended")
        assert suspended["status"] == "executed"
        assert _owner_binding(harness, "b-paper")["status"] == "suspended"
        assert _binding_command(harness, "b-paper", "activate", "paper-reactivate")["status"] == "executed"

        harness.restart()
        reloaded = _owner_binding(harness, "b-paper")
        assert reloaded["status"] == "active" and reloaded["role"] == "paper_owner"
        assert harness.capital_client.get("/api/capital-pools/pool-paper").json()["status"] == "active"
        # Every owner write above carried the caller's JWT, never a service/provisioning token.
        writes = [call for call in harness.owner_calls if call[0] in {"POST", "PATCH"}]
        assert writes and {call[2].removeprefix("Bearer ") for call in writes} == {"op-2:operator"}


class _ExactApprovalReader:
    """Governance reader: only ``approval-exact`` is a valid decision; everything else is absent/revoked."""

    def get(self, decision_id: str) -> Any:
        if decision_id != "approval-exact":
            raise ApprovalInvalid(f"decision {decision_id!r} is absent or revoked")

        class _Evidence:
            def require_valid(self, *, expected: Dict[str, Any], **_: Any) -> None:
                self.seen = expected

        return _Evidence()


@pytest.mark.parametrize("scope", ["canary", "live"])
def test_canary_and_live_binding_activation_require_exact_owner_approval(tmp_path: Path, scope: str) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        capital_guard.configured_approval_reader = lambda domain: _ExactApprovalReader()
        _paper_pool(harness)  # a paper label on the pool is not an exemption for canary/live bindings
        _binding(f"b-{scope}", "pool-paper", role="live_owner", scope=scope, persona_id=f"p-{scope}", capital_sleeve_id=f"s-{scope}")

        for key, params in (("missing", {}), ("revoked", {"approval_decision_id": "approval-revoked"})):
            denied = _binding_command(harness, f"b-{scope}", "activate", f"{scope}-{key}", **params)
            assert denied["status"] == "failed", denied
            assert denied["error"]["downstream_status"] == 403
            assert _owner_binding(harness, f"b-{scope}")["status"] == "pending"

        allowed = _binding_command(harness, f"b-{scope}", "activate", f"{scope}-exact", approval_decision_id="approval-exact")
        assert allowed["status"] == "executed", allowed
        owner = _owner_binding(harness, f"b-{scope}")
        assert owner["status"] == "active" and owner["approval_decision_id"] == "approval-exact"


def test_pool_actions_forward_status_and_unsupported_actions_fail_explicitly(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        pool = {"type": "CapitalPool", "id": "pool-real"}

        def pool_action(action: str, key: str, **params: Any) -> Dict[str, Any]:
            return harness.run_command(
                "CapitalPoolAction", pool, {"action_id": action, "entity_type": "capital-pool", "entity_id": "pool-real", **params}, key=key
            )

        paused = pool_action("pause", "pool-pause")
        assert paused["status"] == "executed", paused["error"]
        assert paused["result"]["pool_state"] == "suspended"
        assert harness.capital_client.get("/api/capital-pools/pool-real").json()["status"] == "suspended"

        # pool-real is not a paper pool: reactivation needs the owner's Governance check.
        capital_guard.configured_approval_reader = lambda domain: _ExactApprovalReader()
        assert pool_action("activate", "pool-activate-denied")["error"]["downstream_status"] == 403
        reactivated = pool_action("activate", "pool-activate", approval_decision_id="approval-exact")
        assert reactivated["status"] == "executed"
        assert harness.capital_client.get("/api/capital-pools/pool-real").json()["status"] == "active"

        for unsupported in ("adjust_budget", "ApprovePool"):
            record = pool_action(unsupported, f"pool-{unsupported}")
            assert record["status"] == "failed"
            assert record["error"]["code"] == "ACTION_UNAVAILABLE"
        assert harness.capital_client.get("/api/capital-pools/pool-real").json()["status"] == "active"


def test_retired_approve_pool_command_fails_without_an_owner_effect(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        before = len(harness.owner_calls)
        record = harness.run_command(
            "ApprovePool", {"type": "CapitalPool", "id": "pool-real"}, {"memo": "approve this pool"}, key="approve-pool"
        )
        assert record["status"] == "failed"
        assert record["error"]["code"] == "ACTION_UNAVAILABLE"
        assert len(harness.owner_calls) == before


def test_viewer_cannot_write_and_forged_body_identity_is_not_forwarded(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        baseline = len(harness.owner_calls)
        body = {"pool_id": "pool-viewer", "name": "Viewer pool", "owner_id": "t", "owner_type": "org", "metadata": {"execution_context": "paper"}}
        denied = harness.client.post("/bff/capital-pools", json=body, headers={**VIEWER, "Idempotency-Key": "viewer-create"})
        assert denied.status_code == 403
        assert len(harness.owner_calls) == baseline  # the owner was never reached

        sent: Dict[str, Any] = {}
        post = command_executor._post_json

        def spy(
            url: str,
            payload: Dict[str, Any],
            auth_token: Optional[str] = None,
            mfa_token: Optional[str] = None,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            sent.update(payload)
            return post(url, payload, auth_token, mfa_token, *args, **kwargs)

        command_executor._post_json = spy
        try:
            forged = {**body, "pool_id": "pool-forged", "actor_id": "attacker", "actor_role": "capital.admin", "tenant_id": "tenant-other"}
            response = harness.client.post("/bff/capital-pools", json=forged, headers={**HEADERS, "Idempotency-Key": "forged-create"})
        finally:
            command_executor._post_json = post
        assert response.status_code == 201, response.text
        assert (sent["actor_id"], sent["actor_role"]) == ("op-2", "operator")
        assert "tenant_id" not in sent


def test_owner_rejection_conflict_and_unavailability_stay_distinct_over_rest(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        # Re-using a pool_id for a different pool is an owner-reported conflict, never an admission success.
        other = {"pool_id": "pool-real", "name": "Other", "owner_id": "x", "owner_type": "org"}
        conflict = harness.client.post("/bff/capital-pools", json=other, headers={**HEADERS, "Idempotency-Key": "conflict-1"})
        assert conflict.status_code == 400 and "already exists" in conflict.text, conflict.text
        # A non-paper active pool without a Governance decision is an owner rejection (403).
        rejected = harness.client.post(
            "/bff/capital-pools",
            json={"pool_id": "pool-unapproved", "name": "Unapproved", "owner_id": "x", "owner_type": "org"},
            headers={**HEADERS, "Idempotency-Key": "reject-1"},
        )
        assert rejected.status_code == 403, rejected.text
        assert harness.capital_client.get("/api/capital-pools/pool-unapproved").status_code == 404
        # An unreachable owner is unavailability (503).
        original = command_executor._post_json

        def unreachable(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("owner down")

        command_executor._post_json = unreachable
        try:
            down = harness.client.post(
                "/bff/capital-pools",
                json={"pool_id": "pool-down", "name": "Down", "owner_id": "x", "owner_type": "org", "metadata": {"execution_context": "paper"}},
                headers={**HEADERS, "Idempotency-Key": "down-1"},
            )
        finally:
            command_executor._post_json = original
        assert down.status_code == 503, down.text


def test_stored_containment_record_is_forwarded_with_the_caller_jwt(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        params = {
            "command": "EmergencyContainment", "entity_type": "Persona", "entity_id": "p-live", "persona_id": "p-live",
            "action": "freeze", "trigger": "hard_risk_breach", "evidence_refs": ["risk-event:42"],
            "two_man_signature_id": "tms-containment", "capital_pool_id": "pool-real",
            "current_weight": 0.10, "target_weight": 0.10, "actor_id": "op-2", "actor_role": "operator",
            "idempotency_key": "containment-1", "request_hash": "containment-1-hash",
        }
        command_id = "cmd-stored-containment"
        harness.command_store.submit_command(
            command_id=command_id,
            command_type=CommandType.EMERGENCY_CONTAINMENT,
            target=TargetObject(type=ObjectType.PERSONA, id="p-live"),
            submitted_at=utc_now(),
            params=params,
            audit_context={"operator_id": "op-2", "reason": "containment"},
        )
        command_service.set_command_auth_context(command_id, {"auth_token": HEADERS["Authorization"]})
        asyncio.run(command_service.process_command(
            command_id, command_store=harness.command_store, read_store=harness.read_surface,
            resolve_execution_params=lambda record: dict(record["params"]),
        ))
        record = harness.command_store.get_command(command_id)
        assert record["status"] == "executed", record["error"]
        assert record["result"]["containment_state"] == "frozen"
        contained = [call for call in harness.owner_calls if call[1].endswith("/api/containments")]
        assert [call[2].removeprefix("Bearer ") for call in contained] == ["op-2:operator"]
        owner = harness.capital_client.get(f"/api/containments/receipts/{command_id}")
        assert owner.status_code == 200 and owner.json()["persona_id"] == "p-live"


def test_real_operator_jwt_forwarding_with_strict_capital_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "capital-bff-jwt-secret-testing-32bytes!!"
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        monkeypatch.setenv("PANTHEON_BFF_JWT_SECRET", secret)
        monkeypatch.setenv("CAPITAL_JWT_SECRET", secret)
        monkeypatch.setenv("CAPITAL_AUTH_DISABLED", "false")
        monkeypatch.setenv("CAPITAL_AUTH_MODE", "strict")
        monkeypatch.setenv("CAPITAL_ALLOWED_CALLER_SERVICES", "control-plane-bff")
        harness.restart()

        from services.control_plane.bff.auth.policy import create_auth_dependencies
        from services.control_plane.bff.auth.handlers import _issue_token

        deps = create_auth_dependencies()
        profile = {
            "identity": "operator",
            "subject": "op-verified-operator",
            "roles": ["operator"],
            "client_id": "bff-dev-operator",
            "tenant_id": "tenant-real-dev",
            "allowed_tenants": ["tenant-real-dev"],
        }
        tok = _issue_token(profile, deps)
        token_str = tok["access_token"]
        headers = {
            "Authorization": f"Bearer {token_str}",
            "Idempotency-Key": "create-pool-jwt",
        }

        body = {
            "pool_id": "pool-jwt",
            "name": "Pool via Real JWT",
            "owner_id": "tenant-real-dev",
            "owner_type": "org",
            "metadata": {"execution_context": "paper"},
        }
        res = harness.client.post("/bff/capital-pools", json=body, headers=headers)
        assert res.status_code == 201, res.text
        assert res.json()["data"]["pool_id"] == "pool-jwt"

        owner_pool = harness.capital_client.get(
            "/api/capital-pools/pool-jwt",
            headers={
                "Authorization": f"Bearer {token_str}",
                "X-Tenant-Id": "tenant-real-dev",
                "X-Pantheon-Service": "control-plane-bff",
            },
        )
        assert owner_pool.status_code == 200
        assert owner_pool.json()["pool_id"] == "pool-jwt"

        pool_calls = [c for c in harness.owner_calls if c[1].endswith("/api/capital-pools") and c[0] == "POST"]
        assert any(c[2] == f"Bearer {token_str}" for c in pool_calls)
        assert all("service:provisioning" not in str(c[2]) for c in harness.owner_calls)

        # Stored PersonaCapitalBinding activation under strict auth
        created_b = command_executor.create_capital_binding(
            {
                "binding_id": "b-strict-paper",
                "persona_id": "p-strict-paper",
                "capital_pool_id": "pool-jwt",
                "role": "paper_owner",
                "allowed_deployment_scope": "paper",
                "actor_id": "op-verified-operator",
                "actor_role": "operator",
            },
            auth_token=token_str,
            tenant_id="tenant-real-dev",
        )
        assert created_b["status"] == "pending"
        b_params = {
            "action_id": "activate",
            "entity_type": "binding",
            "entity_id": "b-strict-paper",
            "actor_id": "op-verified-operator",
            "actor_role": "operator",
            "tenant_id": "tenant-real-dev",
            "idempotency_key": "activate-strict-b",
        }
        act = harness.run_command(
            "PersonaAction",
            {"type": BINDING, "id": "b-strict-paper"},
            b_params,
            key="activate-strict-b",
            headers={"Authorization": f"Bearer {token_str}"},
        )
        assert act["status"] == "executed", act.get("error")
        assert act["result"]["authoritative_readback"]["status"] == "active"

        # Stored CapitalPoolAction pause under strict auth forwards caller JWT and verified tenant
        params = {
            "entity_type": "CapitalPool",
            "entity_id": "pool-jwt",
            "action_id": "pause",
            "actor_id": "op-verified-operator",
            "actor_role": "operator",
            "tenant_id": "tenant-real-dev",
            "idempotency_key": "pause-pool-jwt",
        }
        paused = harness.run_command(
            "CapitalPoolAction",
            {"type": "CapitalPool", "id": "pool-jwt"},
            params,
            key="pause-pool-jwt",
            headers={"Authorization": f"Bearer {token_str}"},
        )
        assert paused["status"] == "executed", paused.get("error")
        assert paused["result"]["pool_state"] == "suspended"

        # Foreign tenant denial under strict auth (403 TENANT_SCOPE_FORBIDDEN)
        foreign_tenant_params = {**params, "tenant_id": "foreign-tenant", "idempotency_key": "foreign-pause"}
        denied_tenant = harness.run_command(
            "CapitalPoolAction",
            {"type": "CapitalPool", "id": "pool-jwt"},
            foreign_tenant_params,
            key="foreign-pause",
            headers={"Authorization": f"Bearer {token_str}"},
        )
        assert denied_tenant["status"] == "failed"
        assert denied_tenant["error"]["downstream_status"] == 403

        # Forged actor denial under strict auth (403 ACTOR_ID_MISMATCH)
        import urllib.error
        from services.control_plane.bff.command_adapters.capital_adapter import CapitalOwnerWriter

        with pytest.raises(urllib.error.HTTPError) as exc_actor:
            CapitalOwnerWriter().pool_action(
                {"action_id": "pause"},
                target_id="pool-jwt",
                actor_id="attacker",
                actor_role="operator",
                auth_token=token_str,
                tenant_id="tenant-real-dev",
            )
        assert exc_actor.value.code == 403

        # Forged role denial under strict auth (403 ACTOR_ROLE_MISMATCH)
        with pytest.raises(urllib.error.HTTPError) as exc_role:
            CapitalOwnerWriter().pool_action(
                {"action_id": "pause"},
                target_id="pool-jwt",
                actor_id="op-verified-operator",
                actor_role="capital.admin",
                auth_token=token_str,
                tenant_id="tenant-real-dev",
            )
        assert exc_role.value.code == 403


def test_headers_transport_preserves_timeout() -> None:
    from unittest.mock import MagicMock, patch
    from services.control_plane.bff.command_adapters import base

    response = MagicMock()
    response.status = 200
    response.read.return_value = b"{}"
    response.headers = {}
    response.__enter__.return_value = response
    with patch.object(base.urllib.request, "urlopen", return_value=response) as transport:
        assert base.http_request_json_with_headers("http://isolated.invalid/metadata", timeout=7) == (200, {}, {})
        assert transport.call_args.kwargs["timeout"] == 7


def test_stored_binding_active_status_rejection_preserved_and_idempotent_reconciled(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        _paper_pool(harness, "pool-active-test")
        _binding("binding-p2-test", "pool-active-test", role="paper_owner", scope="paper")
        # Activate paper binding
        act_res = _binding_command(harness, "binding-p2-test", "activate", "act-p2-test")
        assert act_res["status"] == "executed"
        assert _owner_binding(harness, "binding-p2-test")["status"] == "active"

        # Direct PATCH /api/bindings/{id}/status with status: active is prohibited (must use POST activate)
        direct = harness.capital_client.patch(
            "/api/bindings/binding-p2-test/status",
            json={"actor_id": "op-2", "actor_role": "operator", "status": "active"},
        )
        assert direct.status_code == 400
        assert "Use POST" in direct.json().get("detail", "")

        # Stored PersonaAction update_status with status: active must fail, preserving owner rejection
        res_prohibited = _binding_command(harness, "binding-p2-test", "update_status", "prohibited-active-k", status="active")
        assert res_prohibited["status"] == "failed", "Prohibited active status update must fail, not report executed"

        # Suspend the binding
        res_suspend = _binding_command(harness, "binding-p2-test", "update_status", "suspend-k", status="suspended")
        assert res_suspend["status"] == "executed"
        assert _owner_binding(harness, "binding-p2-test")["status"] == "suspended"

        # Second update_status to suspended: same-status idempotent outcome reconciles to executed
        res_idempotent_same = _binding_command(harness, "binding-p2-test", "update_status", "suspend-k-2", status="suspended")
        assert res_idempotent_same["status"] == "executed"
        assert res_idempotent_same["result"]["authoritative_readback"]["status"] == "suspended"

