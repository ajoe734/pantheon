"""Capital Domain Command Adapter.

``CapitalOwnerWriter`` is the single BFF -> Capital owner forwarder.  The REST
router (injected through ``core/app_factory.py``), the canonical command
executor and this adapter all delegate to it, so every write carries the
caller's verified JWT and the owner remains the only decision point for
approval, risk policy and paper/live classification.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from services.control_plane.bff.capital.service import CapitalValidationError, stable_digest

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    capital_url,
    http_request_json,
)

# Fields the caller may never assert: identity, tenant and idempotency are bound by the BFF/owner.
_BOUND_FIELDS = frozenset({"id", "actor_id", "actor_role", "tenant_id", "idempotency_key", "request_hash"})
# Owner CapitalPool statuses are active / suspended / archived.
_POOL_ACTION_STATUS = {"pause": "suspended", "freeze": "suspended", "activate": "active", "resume": "active", "retire": "archived"}


def _executor() -> Any:
    from services.control_plane.bff import command_executor

    return command_executor


def _request_hash(payload: Dict[str, Any]) -> str:
    return stable_digest({k: v for k, v in payload.items() if k not in {"idempotency_key", "request_hash"}})


def _owner_body(payload: Dict[str, Any], actor_id: str, actor_role: str, key: str) -> Dict[str, Any]:
    body = {k: v for k, v in payload.items() if k not in _BOUND_FIELDS}
    body.update(actor_id=actor_id, actor_role=actor_role)
    if key:
        body.update(idempotency_key=key, request_hash=_request_hash(payload))
    return body


class CapitalOwnerWriter:
    """Forward pool, binding, rebalance and containment writes to the Capital owner."""

    def create_pool(self, payload, *, actor_id, actor_role, auth_token=None, key="", **_) -> Dict[str, Any]:
        pool_id = str(payload.get("pool_id") or payload.get("id") or "").strip()
        if not pool_id:
            raise CapitalValidationError("pool_id is required")
        body = {**_owner_body(payload, actor_id, actor_role, key), "pool_id": pool_id}
        return _executor().create_capital_pool(body, auth_token=auth_token)

    def pool_action(self, payload, *, actor_id, actor_role, target_id, auth_token=None, **_) -> Dict[str, Any]:
        action = str(payload.get("action_id") or "").strip()
        status = _POOL_ACTION_STATUS.get(action.lower())
        if status is None:
            raise ActionUnavailableError(
                f"CapitalPool action {action!r} is not supported by Capital authority.",
                action_id=action,
                entity_type="CapitalPool",
            )
        return self._set_status(
            f"/api/capital-pools/{quote(target_id, safe='')}",
            {"status": status, "approval_decision_id": payload.get("approval_decision_id")},
            actor_id, actor_role, auth_token,
        )

    def activate_binding(self, payload, *, actor_id, actor_role, target_id, auth_token=None, **_) -> Dict[str, Any]:
        path = f"/api/bindings/{quote(target_id, safe='')}"
        body = {"actor_id": actor_id, "actor_role": actor_role, "approval_decision_id": payload.get("approval_decision_id")}
        http_request_json(capital_url(f"{path}/activate"), method="POST", payload=body, auth_token=auth_token)
        return http_request_json(capital_url(path), auth_token=auth_token)

    def binding_status(self, payload, *, actor_id, actor_role, target_id, auth_token=None, **_) -> Dict[str, Any]:
        return self._set_status(
            f"/api/bindings/{quote(target_id, safe='')}", {"status": payload.get("status")},
            actor_id, actor_role, auth_token,
        )

    def create_rebalance(self, payload, *, actor_id, actor_role, auth_token=None, key="", **_) -> Dict[str, Any]:
        body = _owner_body(payload, actor_id, actor_role, key)
        body.setdefault("capital_pool_id", payload.get("pool_id"))
        if payload.get("id") and not payload.get("rebalance_id"):
            body["rebalance_id"] = payload["id"]
        return _executor().create_capital_rebalance_proposal(body, auth_token=auth_token)

    def apply_rebalance(self, payload, *, actor_id, actor_role, target_id, auth_token=None, key="", **_) -> Dict[str, Any]:
        params = {
            "entity_type": "Rebalance",
            "entity_id": target_id,
            "idempotency_key": key,
            "request_hash": _request_hash(payload),
            "approval_ref": payload.get("approval_ref") or "",
            "proposal_version": payload.get("proposal_version"),
            "actor_id": actor_id,
            "actor_role": actor_role,
        }
        return _executor()._execute_approved_rebalance_apply(
            str(payload.get("command_id") or key), params, auth_token=auth_token
        )

    @staticmethod
    def _set_status(path: str, fields: Dict[str, Any], actor_id: str, actor_role: str, auth_token: Optional[str]) -> Dict[str, Any]:
        body = {"actor_id": actor_id, "actor_role": actor_role, **fields}
        http_request_json(capital_url(f"{path}/status"), method="PATCH", payload=body, auth_token=auth_token)
        return http_request_json(capital_url(path), auth_token=auth_token)


class CapitalCommandAdapter(DomainCommandAdapter):
    """Adapter for stored Capital Service authority commands."""

    _HANDLED_COMMANDS = {
        "CapitalPoolAction",
        "RebalanceAction",
        "RebalanceProposal",
        "PatchRebalance",
        "ApprovedApply",
        "EmergencyContainment",
    }

    _HANDLED_ENTITIES = {
        "capitalpool",
        "capital-pool",
        "rebalance",
        "binding",
        "personacapitalbinding",
        "persona-capital-binding",
    }

    writer = CapitalOwnerWriter()

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        normalized_action = str(action_id or "").strip().lower().replace("_", "-")
        if normalized_cmd in self._HANDLED_COMMANDS:
            return True
        if normalized_entity in self._HANDLED_ENTITIES:
            return True
        if normalized_entity == "persona" and (
            normalized_cmd == "EmergencyContainment"
            or normalized_action in {"emergencycontainment", "emergency-containment", "containment"}
        ):
            return True
        return False

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity_type = str(params.get("entity_type") or "").strip().lower().replace("_", "-")
        action_id = str(params.get("action_id") or "").strip()
        entity_id = str(params.get("entity_id") or params.get("pool_id") or params.get("rebalance_id") or params.get("binding_id") or "").strip()

        if command_type == "EmergencyContainment":
            return _executor()._execute_emergency_containment_authority(command_id, params, auth_token=auth_token)
        if command_type == "ApprovedApply":
            return _executor()._execute_approved_rebalance_apply(command_id, params, auth_token=auth_token)
        if entity_type in {"capitalpool", "capital-pool"}:
            return self._pool(command_id, entity_id, action_id, params, auth_token)
        if entity_type == "rebalance" or command_type in {"RebalanceProposal", "PatchRebalance"}:
            return self._rebalance(command_id, action_id, params, auth_token)
        if entity_type in {"binding", "personacapitalbinding", "persona-capital-binding"}:
            return self._binding(command_id, entity_id, action_id, params, auth_token)
        raise ActionUnavailableError(
            f"Capital adapter cannot route entity_type={entity_type!r} action_id={action_id!r}",
            action_id=action_id,
            entity_type=entity_type,
        )

    @staticmethod
    def _ctx(params: Dict[str, Any], auth_token: Optional[str]) -> Dict[str, Any]:
        return {
            "actor_id": str(params.get("actor_id") or ""),
            "actor_role": str(params.get("actor_role") or ""),
            "key": str(params.get("idempotency_key") or ""),
            "auth_token": auth_token,
        }

    def _pool(self, command_id, pool_id, action_id, params, auth_token) -> Dict[str, Any]:
        if not pool_id:
            raise ValueError("CapitalPool action requires a non-empty entity_id (pool_id).")
        readback = self.writer.pool_action(
            {**params, "action_id": action_id}, target_id=pool_id, **self._ctx(params, auth_token)
        )
        return self._receipt(
            command_id, "CapitalPool", pool_id, action_id, "executed", f"/api/capital-pools/{quote(pool_id, safe='')}/status",
            readback, pool_id=pool_id, pool_state=readback.get("status"),
        )

    def _rebalance(self, command_id, action_id, params, auth_token) -> Dict[str, Any]:
        if action_id.lower() not in {"propose", "rebalanceproposal", "create"}:
            raise ActionUnavailableError(
                f"Rebalance action {action_id!r} is not supported by Capital authority; "
                "only proposal creation and ApprovedApply are owner operations.",
                action_id=action_id,
                entity_type="Rebalance",
            )
        body = self.writer.create_rebalance(params, **self._ctx(params, auth_token))
        rebalance_id = str(body.get("rebalance_id") or body.get("id") or "").strip()
        return self._receipt(
            command_id, "Rebalance", rebalance_id, "RebalanceProposal", "created", "/api/rebalances", body, rebalance_id=rebalance_id
        )

    def _binding(self, command_id, binding_id, action_id, params, auth_token) -> Dict[str, Any]:
        if not binding_id:
            raise ValueError("Binding action requires binding_id.")
        ctx = self._ctx(params, auth_token)
        if action_id.lower() == "activate":
            readback = self.writer.activate_binding(params, target_id=binding_id, **ctx)
        else:
            readback = self.writer.binding_status(
                {"status": params.get("status") or action_id}, target_id=binding_id, **ctx
            )
        return self._receipt(
            command_id, "PersonaCapitalBinding", binding_id, action_id, "executed", f"/api/bindings/{quote(binding_id, safe='')}",
            readback, binding_id=binding_id,
        )

    @staticmethod
    def _receipt(command_id, entity_type, entity_id, action_id, status, path, readback, **extra) -> Dict[str, Any]:
        """Receipt whose readback is the owner's own persisted record."""
        return build_domain_receipt(
            command_id=command_id, entity_type=entity_type, entity_id=entity_id, action_id=action_id, status=status,
            dispatch_path=capital_url(path), domain_receipt=readback, authoritative_readback=readback, extra=extra,
        )
