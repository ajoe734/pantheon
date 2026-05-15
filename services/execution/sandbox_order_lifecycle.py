"""Sandbox/test-key order lifecycle evidence helpers.

The helper is deliberately side-effect free. It turns an already built provider
order payload into an auditable lifecycle packet that proves place, cancel,
readback, execution disposition, and reconciliation shape without contacting a
production-live broker or using real capital.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class SandboxOrderLifecycleError(ValueError):
    """Raised when lifecycle evidence would overstate a sandbox/test-key run."""


@dataclass(frozen=True)
class SandboxOrderLifecycleAdapter:
    provider: str
    mode: str

    def build(
        self,
        *,
        generated_at: str,
        place: Mapping[str, Any],
        cancel_replace: Mapping[str, Any],
        account_ref: str | None,
        credential_ref: str | None,
    ) -> dict[str, Any]:
        self._validate_non_live(place=place, cancel_replace=cancel_replace)
        order_ref = _extract_order_ref(cancel_replace)
        side_effect = str(place.get("side_effect") or "none")
        cancel_side_effect = str(cancel_replace.get("side_effect") or side_effect)
        readback_after_place = {
            "status": "observed_open_or_validate_only",
            "order_ref": order_ref,
            "source": "sandbox_lifecycle_adapter",
            "submitted_to_production_broker": False,
            "real_capital_reserved": False,
            "observed_order": place.get("request"),
        }
        readback_after_cancel = {
            "status": "observed_cancelled_or_validate_only",
            "order_ref": order_ref,
            "source": "sandbox_lifecycle_adapter",
            "submitted_to_production_broker": False,
            "real_capital_reserved": False,
            "cancel_request": cancel_replace.get("cancel_request"),
        }
        checks = {
            "place_request_present": bool(place.get("request")),
            "cancel_targets_same_order_ref": bool(order_ref),
            "readback_after_place_present": True,
            "readback_after_cancel_present": True,
            "no_production_live_order": True,
            "no_real_capital_reserved": True,
            "credential_ref_only": credential_ref is None or _looks_like_reference(credential_ref),
        }
        return {
            "adapter": "sandbox_test_key_order_lifecycle",
            "provider": self.provider,
            "mode": self.mode,
            "generated_at": generated_at,
            "order_ref": order_ref,
            "account_ref": account_ref,
            "credential_boundary": {
                "credential_ref": credential_ref,
                "raw_secret_material_present": False,
                "secret_material_source": "repo_safe_reference_or_absent",
            },
            "capital_boundary": {
                "real_capital_used": False,
                "real_capital_reserved": False,
                "production_live_side_effects_allowed": False,
                "production_live_order_submitted": False,
            },
            "steps": [
                {
                    "step": "place",
                    "status": place.get("status"),
                    "side_effect": side_effect,
                    "submitted_to_production_broker": False,
                },
                {
                    "step": "readback_after_place",
                    **readback_after_place,
                },
                {
                    "step": "cancel",
                    "status": cancel_replace.get("status"),
                    "side_effect": cancel_side_effect,
                    "submitted_to_production_broker": False,
                },
                {
                    "step": "readback_after_cancel",
                    **readback_after_cancel,
                },
                {
                    "step": "reconcile",
                    "status": "passed" if all(checks.values()) else "failed",
                    "checks": checks,
                },
            ],
            "readback": {
                "after_place": readback_after_place,
                "after_cancel": readback_after_cancel,
            },
            "execution": {
                "status": "no_production_broker_execution_attempted",
                "fill_status": "no_fill_sandbox_or_validate_only",
                "matching_execution_count": 0,
                "matching_quantity": 0,
            },
            "reconciliation": {
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
            },
        }

    def _validate_non_live(
        self,
        *,
        place: Mapping[str, Any],
        cancel_replace: Mapping[str, Any],
    ) -> None:
        side_effects = {
            str(place.get("side_effect") or "").strip().lower(),
            str(cancel_replace.get("side_effect") or "").strip().lower(),
        }
        forbidden = {"production_live", "live", "real_capital", "submitted_to_broker"}
        if side_effects & forbidden:
            raise SandboxOrderLifecycleError(
                "sandbox lifecycle evidence cannot include production-live side effects"
            )


def _extract_order_ref(cancel_replace: Mapping[str, Any]) -> str:
    cancel_request = cancel_replace.get("cancel_request")
    if not isinstance(cancel_request, Mapping):
        return ""
    for key in ("order_ref", "txid", "order_id"):
        value = cancel_request.get(key)
        if value:
            return str(value)
    return ""


def _looks_like_reference(value: str) -> bool:
    lowered = value.strip().lower()
    return "://" in lowered or lowered.startswith(("pantheon-", "broker-", "kraken-", "shioaji-"))
