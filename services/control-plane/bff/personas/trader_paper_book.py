"""Trader servant paper book provisioning through Capital and Runtime-Manager owners.

Task: PAPER-TRADER-BOOK-PROVISION-20261127 (RC4 / SD-AGORA-SERVANT-CORE-CAPABILITIES-20261127 T7).
On first paper use, the trader's servant persona gets:
- one paper CapitalPool and one paper PersonaCapitalBinding through the Capital owner
- one paper RuntimeBinding through the Runtime-Manager owner (using the dynamic
  runtime-manager bearer auth from PAPER-RUNTIME-MANAGER-DYNAMIC-BEARER-20261125)

Repeated calls are GET-first and idempotent. No canary or live binding can be
created from this path. The starting capital comes from an operator setting
(default TWD 10,000,000 notional, paper only) and is returned with its source.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

from services.control_plane.bff.persona_provisioning import ProvisioningRecord
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
    PersonaProvisioningCoordinator,
    ProvisioningIds,
    TERMINAL_RUNTIME_BINDING_STATUSES,
    deterministic_provisioning_ids,
)
from services.runtime_auth import RuntimeManagerAuthConfig, resolve_runtime_manager_auth

DEFAULT_TRADER_PAPER_STARTING_CAPITAL = 10_000_000.0
DEFAULT_TRADER_PAPER_STARTING_CAPITAL_CURRENCY = "TWD"

TRADER_PAPER_STARTING_CAPITAL_ENV = "PANTHEON_TRADER_PAPER_STARTING_CAPITAL"
TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV = "PANTHEON_TRADER_PAPER_STARTING_CAPITAL_CURRENCY"

TRADER_PAPER_STARTING_CAPITAL_ENV_KEYS: tuple[str, ...] = (
    TRADER_PAPER_STARTING_CAPITAL_ENV,
    "PANTHEON_AGORA_TRADER_PAPER_STARTING_CAPITAL",
)
TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV_KEYS: tuple[str, ...] = (
    TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV,
    "PANTHEON_AGORA_TRADER_PAPER_CURRENCY",
)

# PAPER-TRADER-BOOK-LEGACY-BOOK-REPAIR-20261128: one place for the trader paper
# book default universe per starting-capital currency (#7983 created TWD books
# with 2330.TW) and for the legacy default that books created before #7983 got
# from ``PersonaProvisioningCoordinator._paper_universe`` when symbols were absent.
TRADER_PAPER_BOOK_DEFAULT_UNIVERSE_BY_CURRENCY: Dict[str, tuple[str, ...]] = {
    "TWD": ("2330.TW",),
}
LEGACY_ABSENT_SYMBOLS_DEFAULT_UNIVERSE: tuple[str, ...] = ("SPY",)
TRADER_PAPER_BOOK_IDEMPOTENCY_PREFIX = "trader-paper-book:"
TRADER_PAPER_BOOK_PROVISIONED_STEP = "trader_paper_book_provisioned"

_CURRENCY_RE = re.compile(r"^[A-Z]{3,8}$")
_STAGE_FIELD_NAMES: tuple[str, ...] = (
    "target_stage",
    "stage",
    "deployment_stage",
    "deployment_mode",
    "execution_mode",
    "allowed_deployment_scope",
    "environment",
    "environment_ceiling",
    "capital_mode",
)


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _clean_env(env_map: Mapping[str, str], keys: tuple[str, ...]) -> tuple[str, str]:
    for key in keys:
        raw = str(env_map.get(key) or "").strip()
        if raw and not (raw.startswith("${") and raw.endswith("}")):
            return key, raw
    return keys[0], ""


def resolve_trader_paper_starting_capital(
    env: Optional[Mapping[str, str]] = None,
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the operator setting for a trader's paper book starting capital.

    Never invokes a language model. Returns the numeric notional amount,
    currency, setting name, and provenance source.
    """
    env_map = os.environ if env is None else env
    resolved_as_of = str(as_of or "").strip() or _utc_now_iso()

    amount_key, raw_amount = _clean_env(env_map, TRADER_PAPER_STARTING_CAPITAL_ENV_KEYS)
    if raw_amount:
        try:
            parsed_amount = float(raw_amount.replace(",", "_"))
        except (TypeError, ValueError) as exc:
            raise PersonaProvisioningCoordinationError(
                f"INVALID_TRADER_PAPER_STARTING_CAPITAL: Invalid operator starting capital in "
                f"{amount_key}={raw_amount!r}: must be a positive number",
                reason_code="starting_capital_setting_invalid",
            ) from exc
        if not math.isfinite(parsed_amount) or parsed_amount <= 0.0:
            raise PersonaProvisioningCoordinationError(
                f"INVALID_TRADER_PAPER_STARTING_CAPITAL: Invalid operator starting capital in "
                f"{amount_key}={raw_amount!r}: must be finite and > 0",
                reason_code="starting_capital_setting_invalid",
            )
        amount = float(parsed_amount)
        amount_configured = True
        amount_source = "operator_env"
        amount_source_ref = f"env:{amount_key}"
    else:
        amount = DEFAULT_TRADER_PAPER_STARTING_CAPITAL
        amount_configured = False
        amount_source = "operator_setting_default"
        amount_source_ref = f"default:{amount_key}"

    currency_key, raw_currency = _clean_env(
        env_map, TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV_KEYS
    )
    if raw_currency:
        normalized_currency = raw_currency.upper()
        if raw_currency != normalized_currency or not _CURRENCY_RE.fullmatch(normalized_currency):
            raise PersonaProvisioningCoordinationError(
                f"INVALID_TRADER_PAPER_STARTING_CAPITAL: Invalid operator starting capital currency in "
                f"{currency_key}={raw_currency!r}: must be an uppercase 3-8 letter currency code",
                reason_code="starting_capital_currency_invalid",
            )
        currency = normalized_currency
        currency_configured = True
        currency_source = "operator_env"
        currency_source_ref = f"env:{currency_key}"
    else:
        currency = DEFAULT_TRADER_PAPER_STARTING_CAPITAL_CURRENCY
        currency_configured = False
        currency_source = "operator_setting_default"
        currency_source_ref = f"default:{currency_key}"

    return {
        "amount": amount,
        "currency": currency,
        "notional_only": True,
        "execution_context": "paper",
        "source": amount_source,
        "source_ref": amount_source_ref,
        "setting_name": amount_key,
        "configured": amount_configured,
        "currency_source": currency_source,
        "currency_source_ref": currency_source_ref,
        "currency_setting_name": currency_key,
        "currency_configured": currency_configured,
        "as_of": resolved_as_of,
    }


def validate_paper_only_stage(**stage_fields: Any) -> None:
    """Reject any request or parameter that attempts to target canary, live, or non-paper."""
    for field_name, raw_value in stage_fields.items():
        if raw_value is None:
            continue
        if isinstance(raw_value, bool) or not isinstance(raw_value, str):
            raise PersonaProvisioningCoordinationError(
                f"PAPER_ONLY_TRADER_BOOK_STAGE_VIOLATION: Trader paper book {field_name} must be 'paper'; "
                f"got {raw_value!r}",
                reason_code="non_paper_stage_forbidden",
            )
        cleaned = raw_value.strip().lower()
        if not cleaned or cleaned != "paper":
            raise PersonaProvisioningCoordinationError(
                f"PAPER_ONLY_TRADER_BOOK_STAGE_VIOLATION: Trader paper book provisioning is paper-only; "
                f"{field_name}={raw_value!r} cannot create canary or live bindings on this path",
                reason_code="non_paper_stage_forbidden",
            )


def validate_servant_persona_paper_ceiling(
    persona: Optional[Mapping[str, Any]],
    *,
    tenant_id: Optional[str] = None,
    persona_id: Optional[str] = None,
) -> None:
    """Fail closed if a persona record carries non-paper ceiling, live authority, or foreign tenant."""
    if not isinstance(persona, Mapping):
        return
    meta = persona.get("metadata")
    meta_map = meta if isinstance(meta, Mapping) else {}

    rec_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
    expected_persona = str(persona_id or rec_id or "").strip()
    if rec_id and expected_persona and rec_id != expected_persona:
        raise PersonaProvisioningCoordinationError(
            f"PAPER_ONLY_SERVANT_CEILING_VIOLATION: Persona record identity {rec_id!r} "
            f"does not match requested {expected_persona!r}",
            reason_code="persona_identity_mismatch",
        )

    expected_tenant = str(tenant_id or "").strip()
    if expected_tenant:
        for tenant_val in (
            persona.get("tenant_id"),
            persona.get("tenantId"),
            meta_map.get("tenant_id"),
            meta_map.get("tenantId"),
        ):
            if tenant_val not in (None, "") and str(tenant_val).strip() != expected_tenant:
                raise PersonaProvisioningCoordinationError(
                    f"PAPER_ONLY_SERVANT_CEILING_VIOLATION: Persona {expected_persona!r} "
                    f"does not belong to tenant {expected_tenant!r}",
                    reason_code="persona_tenant_mismatch",
                )

    for field_name in _STAGE_FIELD_NAMES:
        for container in (persona, meta_map):
            val = container.get(field_name)
            if val not in (None, ""):
                try:
                    validate_paper_only_stage(**{field_name: str(val)})
                except PersonaProvisioningCoordinationError as exc:
                    raise PersonaProvisioningCoordinationError(
                        f"PAPER_ONLY_SERVANT_CEILING_VIOLATION: {exc}",
                        reason_code="persona_ceiling_not_paper",
                    ) from exc

    for live_flag in (
        "live_capital_enabled",
        "live_write_enabled",
        "canary_execution_enabled",
        "live_broker_enabled",
    ):
        if persona.get(live_flag) is True or meta_map.get(live_flag) is True:
            raise PersonaProvisioningCoordinationError(
                f"PAPER_ONLY_SERVANT_CEILING_VIOLATION: Persona {expected_persona!r} has "
                f"{live_flag}=True; refusing trader paper book provisioning",
                reason_code="persona_ceiling_not_paper",
            )

    for auth_container in (
        persona,
        meta_map,
        meta_map.get("policy") if isinstance(meta_map.get("policy"), Mapping) else {},
    ):
        if not isinstance(auth_container, Mapping):
            continue
        exec_auth = auth_container.get("execution_authority")
        if exec_auth not in (None, "") and str(exec_auth).strip().lower() not in {"none", "paper"}:
            raise PersonaProvisioningCoordinationError(
                f"PAPER_ONLY_SERVANT_CEILING_VIOLATION: Persona {expected_persona!r} has "
                f"execution_authority={exec_auth!r}; trader paper book requires paper ceiling "
                "with no live/canary authority",
                reason_code="persona_ceiling_not_paper",
            )


def trader_paper_book_default_universe(currency: Any) -> tuple[str, ...]:
    """Default trader paper book universe for ``currency`` (empty when none is defined)."""
    return TRADER_PAPER_BOOK_DEFAULT_UNIVERSE_BY_CURRENCY.get(
        str(currency or "").strip().upper(), ()
    )


def is_trader_paper_book_ledger_record(record: Any) -> bool:
    """True for a ``bff.persona_provisioning`` row written for a trader servant paper book.

    Such a row only leases the book's Registry/Governance/Deployment authorities;
    it is not a Persona provisioning request and must never be projected onto
    the servant Persona record. Both the deterministic idempotency key and the
    book mandate must match, so a Persona request can never opt out by key alone.
    The mandate (not ``book_kind``) is required because the hosted rows written
    before #7983 carry no ``book_kind``/``persona_class``; a row that does state
    a ``book_kind`` must state the trader book kind.
    """
    key = str(getattr(record, "idempotency_key", None) or "").strip()
    if not key.startswith(TRADER_PAPER_BOOK_IDEMPOTENCY_PREFIX):
        return False
    payload = getattr(record, "request_payload", None)
    if not isinstance(payload, Mapping):
        return False
    if str(payload.get("mandate") or "").strip() != "user_private_agora_servant_paper_book":
        return False
    book_kind = str(payload.get("book_kind") or "").strip()
    return book_kind in {"", "trader_servant_paper_book"}


def _projection_symbols(projection: Mapping[str, Any]) -> tuple[str, ...]:
    metadata = projection.get("metadata")
    meta = metadata if isinstance(metadata, Mapping) else {}
    symbol = str(projection.get("symbol") or meta.get("symbol") or "").strip()
    if symbol:
        return (symbol,)
    raw = projection.get("symbols") or meta.get("symbols")
    if isinstance(raw, (list, tuple)):
        return tuple(str(item or "").strip() for item in raw)
    return ()


def _is_legacy_universe_for_currency(symbols: tuple[str, ...], currency: Any) -> bool:
    """True when a book's projected universe is the legacy absent-symbols default
    while its currency has a different book default universe (a pre-#7983 TWD book)."""
    default_universe = trader_paper_book_default_universe(currency)
    return bool(
        default_universe
        and tuple(s.upper() for s in symbols) == LEGACY_ABSENT_SYMBOLS_DEFAULT_UNIVERSE
        and default_universe != LEGACY_ABSENT_SYMBOLS_DEFAULT_UNIVERSE
    )


def _is_market_admission_pause(binding: Mapping[str, Any]) -> bool:
    """True for a binding the paper fleet reconciler paused on market admission.

    The reconciler records ``session_admission.reason_code`` ``market_input_*``
    and auto-resumes such pauses itself; operator pauses use another reason
    and are never resumed here.
    """
    status = str(binding.get("status") or binding.get("state") or "").strip().lower()
    if status != "paused":
        return False
    metadata = binding.get("metadata")
    meta = metadata if isinstance(metadata, Mapping) else {}
    session_admission = meta.get("session_admission")
    if not isinstance(session_admission, Mapping):
        return False
    return str(session_admission.get("reason_code") or "").startswith("market_input_")


def trader_paper_book_provisioning_record(
    tenant_id: str,
    persona_id: str,
    *,
    requested_by: str,
    starting_capital: Mapping[str, Any],
    created_at: Optional[str] = None,
) -> ProvisioningRecord:
    """Deterministic ProvisioningRecord for a trader servant persona's paper book."""
    clean_tenant = str(tenant_id or "").strip()
    clean_persona = str(persona_id or "").strip()
    clean_requester = str(requested_by or "").strip() or "control-plane-bff"
    currency = str(
        starting_capital.get("currency") or DEFAULT_TRADER_PAPER_STARTING_CAPITAL_CURRENCY
    )
    payload = {
        "name": f"{clean_persona} trader paper book",
        "mandate": "user_private_agora_servant_paper_book",
        "persona_class": "agora_servant",
        "book_kind": "trader_servant_paper_book",
        "risk": "low",
        "currency": currency,
        "budget": float(starting_capital.get("amount") or DEFAULT_TRADER_PAPER_STARTING_CAPITAL),
        "requested_by": clean_requester,
        "execution_context": "paper",
        "environment_ceiling": "paper",
        **(
            {"symbols": list(default_universe)}
            if (default_universe := trader_paper_book_default_universe(currency))
            else {}
        ),
    }
    request_hash = hashlib.sha256(
        json.dumps(
            {"tenant_id": clean_tenant, "persona_id": clean_persona, "scope": "trader_paper_book"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    ts = str(created_at or "").strip() or "2026-11-27T00:00:00Z"
    return ProvisioningRecord(
        tenant_id=clean_tenant,
        idempotency_key=f"{TRADER_PAPER_BOOK_IDEMPOTENCY_PREFIX}{clean_tenant}:{clean_persona}",
        request_hash=request_hash,
        normalized_name=f"agora-servant-{clean_persona}",
        persona_id=clean_persona,
        request_payload=payload,
        created_at=ts,
        updated_at=ts,
    )


def trader_paper_book_ids(tenant_id: str, persona_id: str) -> ProvisioningIds:
    """Derive deterministic owner IDs for `(tenant_id, persona_id)`."""
    dummy = ProvisioningRecord(
        tenant_id=str(tenant_id or "").strip(),
        idempotency_key=f"{TRADER_PAPER_BOOK_IDEMPOTENCY_PREFIX}{tenant_id}:{persona_id}",
        request_hash="0" * 64,
        normalized_name=f"agora-servant-{persona_id}",
        persona_id=str(persona_id or "").strip(),
        request_payload={},
    )
    return deterministic_provisioning_ids(dummy)


def trader_paper_runtime_id(ids: ProvisioningIds) -> str:
    return f"rt-persona-paper-{ids.token}"


def _resolve_dynamic_runtime_manager_auth(
    *,
    tenant_id: str,
    actor_id: str,
    env: Optional[Mapping[str, str]] = None,
) -> RuntimeManagerAuthConfig:
    """Resolve dynamic runtime-manager bearer auth (PAPER-RUNTIME-MANAGER-DYNAMIC-BEARER-20261125)."""
    env_map = os.environ if env is None else env
    auth = resolve_runtime_manager_auth(env=env_map)
    # When runtime-manager runs in strict JWT mode and shares PANTHEON_BFF_JWT_SECRET
    # without a separate PANTHEON_RUNTIME_JWT_SECRET variable exported into the BFF,
    # mint fresh short-lived HS256 JWTs dynamically on each request.
    if (
        auth.source == "default"
        or (
            auth.source == "env"
            and auth._static_token == "runtime-control-internal"
            and str(env_map.get("PANTHEON_RUNTIME_AUTH_MODE") or "").strip().lower() == "strict"
        )
    ):
        bff_secret = str(
            env_map.get("PANTHEON_RUNTIME_MANAGER_JWT_SECRET")
            or env_map.get("PANTHEON_RUNTIME_JWT_SECRET")
            or env_map.get("PANTHEON_BFF_JWT_SECRET")
            or env_map.get("PANTHEON_DEV_BFF_JWT_SECRET")
            or ""
        ).strip()
        if bff_secret and str(env_map.get("PANTHEON_RUNTIME_AUTH_MODE") or "").strip().lower() == "strict":
            return RuntimeManagerAuthConfig(
                token="",
                source="jwt_mint",
                used_default=False,
                jwt_secret=bff_secret,
                jwt_issuer=str(env_map.get("PANTHEON_BFF_JWT_ISSUER") or "").strip() or None,
                jwt_audience=str(env_map.get("PANTHEON_BFF_JWT_AUDIENCE") or "").strip() or None,
                actor_id=actor_id,
                roles=("operator", "admin", "service"),
                tenant_id=tenant_id,
            )
    return auth


def _build_runtime_manager_client(
    *,
    tenant_id: str,
    actor_id: str,
    explicit_client: Optional[Any] = None,
    client_factory: Optional[Callable[[], Any]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Any:
    if explicit_client is not None:
        return explicit_client
    auth = _resolve_dynamic_runtime_manager_auth(
        tenant_id=tenant_id,
        actor_id=actor_id,
        env=env,
    )
    if client_factory is not None:
        client = client_factory()
        if hasattr(client, "_auth"):
            try:
                client._bearer_token = auth
            except Exception:
                pass
        return client
    from . import service as personas_service

    client = personas_service._runtime_manager_client()
    if hasattr(client, "_auth"):
        try:
            client._bearer_token = auth
        except Exception:
            pass
    return client


def _list_pool_runtime_bindings(runtime_client: Any, capital_pool_id: str) -> List[Dict[str, Any]]:
    list_by_pool = getattr(runtime_client, "list_by_pool", None)
    if callable(list_by_pool):
        items = list_by_pool(capital_pool_id) or []
        return [dict(item) for item in items if isinstance(item, Mapping)]
    get_active = getattr(runtime_client, "get_active_for_pool", None)
    if callable(get_active):
        active = get_active(capital_pool_id)
        return [dict(active)] if isinstance(active, Mapping) else []
    raise PersonaProvisioningCoordinationError(
        "Runtime-Manager client does not support list_by_pool or get_active_for_pool",
        reason_code="runtime_manager_unavailable",
    )


def _validate_runtime_binding_readback(
    binding: Mapping[str, Any],
    *,
    tenant_id: str,
    persona_id: str,
    capital_pool_id: str,
    persona_capital_binding_id: str,
    require_active: bool = True,
) -> Dict[str, Any]:
    binding_id = str(
        binding.get("binding_id")
        or binding.get("runtime_binding_id")
        or binding.get("id")
        or ""
    ).strip()
    if not binding_id:
        raise PersonaProvisioningCoordinationError(
            "RuntimeBinding readback has no binding_id",
            reason_code="runtime_binding_invalid",
        )
    if str(binding.get("capital_pool_id") or "").strip() != capital_pool_id:
        raise PersonaProvisioningCoordinationError(
            f"RuntimeBinding {binding_id} capital_pool_id does not match {capital_pool_id!r}",
            reason_code="runtime_binding_pool_mismatch",
        )
    pcb_id = str(binding.get("persona_capital_binding_id") or "").strip()
    if pcb_id and pcb_id != persona_capital_binding_id:
        raise PersonaProvisioningCoordinationError(
            f"RuntimeBinding {binding_id} persona_capital_binding_id {pcb_id!r} "
            f"does not match {persona_capital_binding_id!r}",
            reason_code="runtime_binding_capital_binding_mismatch",
        )
    for stage_key in ("deployment_mode", "execution_mode", "target_stage", "deployment_stage"):
        stage_val = binding.get(stage_key)
        if stage_val not in (None, "") and str(stage_val).strip().lower() != "paper":
            raise PersonaProvisioningCoordinationError(
                f"RuntimeBinding {binding_id} has {stage_key}={stage_val!r}; "
                "trader paper book forbids canary and live bindings",
                reason_code="non_paper_runtime_binding",
            )
    status = str(binding.get("status") or binding.get("state") or "").strip().lower()
    if status != "active" and (require_active or status != "paused"):
        raise PersonaProvisioningCoordinationError(
            f"RuntimeBinding {binding_id} status is {status!r}, expected 'active'",
            reason_code="runtime_binding_not_active",
        )
    meta = binding.get("metadata")
    meta_map = meta if isinstance(meta, Mapping) else {}
    binding_tenant = str(meta_map.get("tenant_id") or binding.get("tenant_id") or "").strip()
    if binding_tenant and binding_tenant != tenant_id:
        raise PersonaProvisioningCoordinationError(
            f"RuntimeBinding {binding_id} belongs to tenant {binding_tenant!r}, expected {tenant_id!r}",
            reason_code="runtime_binding_tenant_mismatch",
        )
    for persona_key in ("persona_id", "sponsor_persona_id"):
        recorded_persona = str(meta_map.get(persona_key) or binding.get(persona_key) or "").strip()
        if recorded_persona and recorded_persona != persona_id:
            raise PersonaProvisioningCoordinationError(
                f"RuntimeBinding {binding_id} belongs to persona {recorded_persona!r}, expected {persona_id!r}",
                reason_code="runtime_binding_persona_mismatch",
            )
    normalized = deepcopy(dict(binding))
    normalized["binding_id"] = binding_id
    return normalized


def _transport_supports_deploy_authorities(
    coordinator: PersonaProvisioningCoordinator,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """True when Registry, Governance, and Deployment owners are configured for plan admission."""
    transport = getattr(coordinator, "transport", None)
    if transport is None:
        return False
    env_map = os.environ if env is None else env
    owner_envs = getattr(transport, "_OWNER_ENVIRONMENTS", None)
    if isinstance(owner_envs, Mapping):
        for owner in ("registry", "governance", "deployment"):
            env_names = owner_envs.get(owner) or ()
            if not any(str(env_map.get(name) or "").strip() for name in env_names):
                return False
        return True
    # Custom test transport injected directly into PersonaProvisioningCoordinator.
    owners = getattr(transport, "owners", None)
    if isinstance(owners, (Mapping, set, frozenset, list, tuple)):
        return all(owner in owners for owner in ("registry", "governance", "deployment"))
    return callable(getattr(transport, "get", None)) and callable(
        getattr(transport, "post", None)
    )


def _transport_supports_paper_risk_gate(
    coordinator: PersonaProvisioningCoordinator,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """True when Capital and Governance owners are configured for P-4 paper risk gate resolution."""
    transport = getattr(coordinator, "transport", None)
    if transport is None:
        return False
    env_map = os.environ if env is None else env
    owner_envs = getattr(transport, "_OWNER_ENVIRONMENTS", None)
    if isinstance(owner_envs, Mapping):
        for owner in ("capital", "governance"):
            env_names = owner_envs.get(owner) or ()
            if not any(str(env_map.get(name) or "").strip() for name in env_names):
                return False
        return True
    owners = getattr(transport, "owners", None)
    if isinstance(owners, (Mapping, set, frozenset, list, tuple)):
        return all(owner in owners for owner in ("capital", "governance"))
    return False


def _resolve_paper_book_risk_profile(
    persona_record: Optional[Mapping[str, Any]],
    capital_pool: Optional[Mapping[str, Any]],
) -> str:
    meta = (
        persona_record.get("metadata")
        if isinstance(persona_record, Mapping) and isinstance(persona_record.get("metadata"), Mapping)
        else {}
    )
    pool_meta = (
        capital_pool.get("metadata")
        if isinstance(capital_pool, Mapping) and isinstance(capital_pool.get("metadata"), Mapping)
        else {}
    )
    for raw in (
        pool_meta.get("requested_risk_profile"),
        meta.get("requested_risk_profile"),
        meta.get("risk_level"),
        persona_record.get("risk_level") if isinstance(persona_record, Mapping) else None,
        persona_record.get("risk") if isinstance(persona_record, Mapping) else None,
    ):
        clean = str(raw or "").strip().lower()
        if clean in {"low", "medium", "high"}:
            return clean
    return "low"


def _ensure_paper_book_risk_policy(
    coordinator: PersonaProvisioningCoordinator,
    *,
    tenant_id: str,
    persona_id: str,
    ids: ProvisioningIds,
    requested_by: str,
    capital_pool: Mapping[str, Any],
    persona_record: Optional[Mapping[str, Any]] = None,
    persona_write_owner: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Ensure the servant paper pool has a P-4 gate-approved risk policy mapping when Governance is configured.

    GET-first and idempotent: when the pool already carries a bound active RiskPolicy and
    gate-approved ``capital_pool_activation`` ApprovalDecision, zero POST/PATCH calls are made.
    Otherwise obtains a risk-officer draft and automated paper risk gate approval through
    ``coordinator.repair_paper_pool_risk_policy`` (Pass 1 proposes + gate-approves; Pass 2
    binds the gate-approved decision onto the CapitalPool). No human approval in paper and
    no hardcoded limits (P-4).
    """
    if not _transport_supports_paper_risk_gate(coordinator, env=env):
        return deepcopy(dict(capital_pool)), None

    from services.capital.risk_policy import resolve_pool_risk_policy_binding

    requested_profile = _resolve_paper_book_risk_profile(persona_record, capital_pool)
    pool_with_digest = coordinator._pool_with_authoritative_approval_digest(
        capital_pool,
        tenant_id=tenant_id,
    )
    current_resolution = resolve_pool_risk_policy_binding(
        capital_pool_id=ids.capital_pool_id,
        pool=pool_with_digest,
        expected_tenant_id=tenant_id,
        expected_stage="paper",
        requested_profile=requested_profile,
        require_approved_profile_mapping=True,
        approval_reader=coordinator._governance_approval_reader(),
        require_active=True,
        require_limits=("gross_limit",),
        raise_on_error=False,
    )
    if current_resolution.resolved and current_resolution.profile_mapping_approval_id:
        return deepcopy(dict(capital_pool)), current_resolution.to_dict()

    original_owner_provider = coordinator.persona_owner_provider

    def _fallback_owner_provider() -> Any:
        owner = original_owner_provider() if callable(original_owner_provider) else None
        if owner is not None:
            return owner
        if persona_write_owner is not None:
            return persona_write_owner
        if isinstance(persona_record, Mapping):
            class _StaticPersonaReader:
                @staticmethod
                def get_persona(pid: str) -> Optional[Mapping[str, Any]]:
                    rec_id = str(persona_record.get("persona_id") or persona_record.get("id") or "").strip()
                    if not rec_id or rec_id == pid:
                        return deepcopy(dict(persona_record))
                    return None
            return _StaticPersonaReader()
        return None

    coordinator.persona_owner_provider = _fallback_owner_provider
    try:
        repair_result = coordinator.repair_paper_pool_risk_policy(
            tenant_id=tenant_id,
            persona_id=persona_id,
            capital_pool_id=ids.capital_pool_id,
            binding_id=ids.persona_capital_binding_id,
            requested_by=requested_by,
            requested_profile=requested_profile,
            require_approved_profile_mapping=True,
        )
        owner_req = repair_result.get("owner_decision_request")
        after_res = repair_result.get("after_resolution")
        if (
            isinstance(owner_req, Mapping)
            and owner_req.get("paper_gate_status") == "decided_approved_by_automated_gate"
            and not (isinstance(after_res, Mapping) and after_res.get("resolved") is True)
        ):
            repair_result = coordinator.repair_paper_pool_risk_policy(
                tenant_id=tenant_id,
                persona_id=persona_id,
                capital_pool_id=ids.capital_pool_id,
                binding_id=ids.persona_capital_binding_id,
                requested_by=requested_by,
                requested_profile=requested_profile,
                require_approved_profile_mapping=True,
            )
            after_res = repair_result.get("after_resolution")
    finally:
        coordinator.persona_owner_provider = original_owner_provider

    updated_pool = (
        deepcopy(dict(repair_result["capital_pool"]))
        if isinstance(repair_result.get("capital_pool"), Mapping)
        else deepcopy(dict(capital_pool))
    )
    if not (isinstance(after_res, Mapping) and after_res.get("resolved") is True):
        gate_status = (
            str(owner_req.get("paper_gate_status") or owner_req.get("governance_proposal_status") or "")
            if isinstance(owner_req, Mapping)
            else ""
        )
        reason = (
            str((after_res or {}).get("reason") or "")
            if isinstance(after_res, Mapping)
            else "paper risk policy mapping unresolved"
        )
        raise PersonaProvisioningCoordinationError(
            f"Trader paper book risk policy gate did not resolve for pool {ids.capital_pool_id}: "
            f"{reason} (gate_status={gate_status or 'unresolved'})",
            reason_code="trader_paper_book_risk_policy_unresolved",
        )
    return updated_pool, deepcopy(dict(after_res))


def _coordinate_paper_book_deploy_authorities(
    coordinator: PersonaProvisioningCoordinator,
    record: ProvisioningRecord,
    ids: ProvisioningIds,
) -> ProvisioningRecord:
    """Ensure the paper-only StrategySpec, StrategyArtifact, ApprovalDecision, and DeploymentPlan exist."""
    active, _ = coordinator.store.reserve(
        tenant_id=record.tenant_id,
        idempotency_key=record.idempotency_key,
        request_hash=record.request_hash,
        normalized_name=record.normalized_name,
        persona_id=record.persona_id,
        request_payload=record.request_payload,
    )
    leased = coordinator.store.acquire(
        active.tenant_id,
        active.idempotency_key,
        lease_owner=coordinator.lease_owner,
        lease_seconds=coordinator.lease_seconds,
    )
    if leased is None:
        raise PersonaProvisioningCoordinationError(
            "Trader paper book provisioning record is leased by another coordinator",
            reason_code="provisioning_lease_busy",
        )
    try:
        leased = coordinator._coordinate_strategy_spec(leased, ids, baseline=True)
        leased = coordinator._coordinate_approval_proposal(leased, ids, baseline=True)
        leased = coordinator._coordinate_approval_review(leased, ids, baseline=True)
        leased = coordinator._coordinate_approval_decision(leased, ids, baseline=True)
        leased = coordinator._coordinate_registry_approval(leased, ids, baseline=True)
        leased = coordinator._coordinate_strategy_artifact(leased, ids, baseline=True)
        leased = coordinator._coordinate_approval_proposal(
            leased, ids, baseline=True, strategy_artifact=True
        )
        leased = coordinator._coordinate_approval_review(
            leased, ids, baseline=True, strategy_artifact=True
        )
        leased = coordinator._coordinate_approval_decision(
            leased, ids, baseline=True, strategy_artifact=True
        )
        leased = coordinator._coordinate_registry_approval(
            leased, ids, baseline=True, strategy_artifact=True
        )
        leased = coordinator._coordinate_strategy_spec(leased, ids)
        leased = coordinator._coordinate_approval_proposal(leased, ids)
        leased = coordinator._coordinate_approval_review(leased, ids)
        leased = coordinator._coordinate_approval_decision(leased, ids)
        leased = coordinator._coordinate_registry_approval(leased, ids)
        leased = coordinator._coordinate_strategy_artifact(leased, ids)
        leased = coordinator._coordinate_approval_proposal(
            leased, ids, strategy_artifact=True
        )
        leased = coordinator._coordinate_approval_review(
            leased, ids, strategy_artifact=True
        )
        leased = coordinator._coordinate_approval_decision(
            leased, ids, strategy_artifact=True
        )
        leased = coordinator._coordinate_registry_approval(
            leased, ids, strategy_artifact=True
        )
        leased = coordinator._coordinate_deployment_plan(leased, ids)
        return leased
    finally:
        try:
            coordinator.store.release(
                leased,
                lease_owner=coordinator.lease_owner,
                lease_seconds=coordinator.lease_seconds,
            )
        except Exception:
            pass


def _effective_starting_capital(
    configured: Mapping[str, Any],
    capital_pool: Mapping[str, Any],
) -> Dict[str, Any]:
    """Combine the operator starting capital setting with the CapitalPool readback."""
    result = deepcopy(dict(configured))
    pool_budget = capital_pool.get("budget")
    if isinstance(pool_budget, (int, float)) and not isinstance(pool_budget, bool) and pool_budget > 0:
        result["amount"] = float(pool_budget)
    pool_currency = str(capital_pool.get("currency") or "").strip().upper()
    if pool_currency:
        result["currency"] = pool_currency
    result["capital_pool_id"] = str(capital_pool.get("pool_id") or "")
    result["owner_readback_verified"] = True
    return result


def _binding_has_executable_projection(
    binding: Mapping[str, Any],
    *,
    ids: ProvisioningIds,
    expected_currency: str = "",
) -> bool:
    """True when ``binding`` already carries the approved artifact projection required by desired-state."""
    from services.control_plane.governance.deployment_plan import StagePlanner

    metadata = binding.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    if str(metadata.get("book_kind") or "").strip() != "trader_servant_paper_book":
        return False
    strategy_id = str(
        binding.get("strategy_id") or metadata.get("strategy_id") or ""
    ).strip()
    if not strategy_id or strategy_id != ids.strategy_id:
        return False
    object_store = binding.get("object_store") or metadata.get("object_store")
    if not isinstance(object_store, Mapping):
        return False
    artifact_version = str(binding.get("artifact_version") or ids.version).strip()
    metadata_key, artifact_key = StagePlanner.build_object_store_keys(
        strategy_id=strategy_id,
        version=artifact_version,
    )
    projected_metadata = object_store.get(metadata_key)
    if isinstance(projected_metadata, str):
        try:
            projected_metadata = json.loads(projected_metadata)
        except json.JSONDecodeError:
            return False
    if not isinstance(projected_metadata, Mapping):
        return False
    if (
        not str(projected_metadata.get("checksum") or "").strip()
        or projected_metadata.get("registry_id") != ids.strategy_artifact_id
        or projected_metadata.get("artifact_state") != "approved"
        or projected_metadata.get("approval_decision_id")
        != ids.strategy_artifact_approval_decision_id
        or projected_metadata.get("deployment_plan_id") != ids.deployment_plan_id
    ):
        return False
    projected_artifact = object_store.get(artifact_key)
    if not (
        (isinstance(projected_artifact, str) and projected_artifact.strip())
        or isinstance(projected_artifact, Mapping)
    ):
        return False
    symbol = str(binding.get("symbol") or metadata.get("symbol") or "").strip()
    if not symbol:
        pool = binding.get("symbols") or metadata.get("symbols")
        names = (
            [str(s or "").strip() for s in pool]
            if isinstance(pool, (list, tuple))
            else []
        )
        if not (len(names) > 1 and all(names) and len(set(names)) == len(names)):
            return False
    if _is_legacy_universe_for_currency(_projection_symbols(binding), expected_currency):
        # A pre-#7983 TWD book projected on the legacy SPY default is not executable
        # on the Taiwan market input; ensure repairs it in place.
        return False
    market_data_policy = (
        binding.get("market_data_policy") or metadata.get("market_data_policy")
    )
    if not isinstance(market_data_policy, Mapping):
        return False
    if str(market_data_policy.get("owner") or "").strip() != "source-ingest":
        return False
    min_closes = market_data_policy.get("minimum_closes")
    max_age = market_data_policy.get("max_age_seconds")
    if (
        isinstance(min_closes, bool)
        or not isinstance(min_closes, int)
        or min_closes < 2
        or isinstance(max_age, bool)
        or not isinstance(max_age, int)
        or max_age < 1
    ):
        return False
    return True


def _resolve_paper_book_execution_projection(
    coordinator: PersonaProvisioningCoordinator,
    record: ProvisioningRecord,
    ids: ProvisioningIds,
    *,
    expected_currency: str = "",
    coordinate_authorities: bool = True,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve the approved artifact projection via StagePlanner.build_execution_projection.

    Fails closed when Registry, Governance, or Deployment owners are unconfigured
    or when the authoritative StrategyArtifact RegistryEntry or DeploymentPlan
    receipt is absent, incomplete, or not approved.
    """
    if not _transport_supports_deploy_authorities(coordinator, env=env):
        raise PersonaProvisioningCoordinationError(
            "Registry, Governance, and Deployment owners must be configured to resolve "
            "the trader paper book execution projection",
            reason_code="deploy_authorities_unavailable",
        )
    plan_receipt = coordinator._owner_get(
        "deployment", f"/api/deployment/plans/{ids.deployment_plan_id}"
    )
    reg_receipt = coordinator._owner_get(
        "registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}"
    )
    reg_entry = (
        reg_receipt.get("entry")
        if isinstance(reg_receipt, Mapping) and isinstance(reg_receipt.get("entry"), Mapping)
        else reg_receipt
    )
    authorities_ready = (
        isinstance(plan_receipt, Mapping)
        and plan_receipt.get("plan_id") == ids.deployment_plan_id
        and plan_receipt.get("status") in {"approved", "executing", "executed"}
        and isinstance(reg_entry, Mapping)
        and reg_entry.get("registry_id") == ids.strategy_artifact_id
        and reg_entry.get("artifact_state") == "approved"
        and reg_entry.get("approval_decision_id")
        == ids.strategy_artifact_approval_decision_id
    )
    if not authorities_ready and coordinate_authorities:
        leased = _coordinate_paper_book_deploy_authorities(coordinator, record, ids)
        plan_ref = leased.references.get("deployment_plan")
        reg_ref = leased.references.get("strategy_artifact_approved")
        if isinstance(plan_ref, Mapping):
            plan_receipt = plan_ref
        else:
            plan_receipt = coordinator._owner_get(
                "deployment", f"/api/deployment/plans/{ids.deployment_plan_id}"
            )
        if isinstance(reg_ref, Mapping):
            reg_receipt = reg_ref
        else:
            reg_receipt = coordinator._owner_get(
                "registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}"
            )
    projection = coordinator.build_paper_execution_projection_metadata(
        record,
        ids,
        deployment_plan_receipt=plan_receipt if isinstance(plan_receipt, Mapping) else None,
        registry_receipt=reg_receipt if isinstance(reg_receipt, Mapping) else None,
    )
    _fail_closed_on_unapproved_book_universe(projection, expected_currency=expected_currency)
    return projection


def _fail_closed_on_unapproved_book_universe(
    projection: Mapping[str, Any],
    *,
    expected_currency: str,
) -> None:
    """Refuse to project an approved artifact whose universe is not the book's currency universe.

    Books created before #7983 had no ``symbols`` in their ledger request, so
    ``_paper_universe`` minted and approved their artifact on the legacy SPY
    default. Only an owner-approved revision may change the approved universe;
    the BFF never rewrites the projected symbol, so ensure refuses without any
    Runtime-Manager or ledger write.
    """
    symbols = _projection_symbols(projection)
    if not _is_legacy_universe_for_currency(symbols, expected_currency):
        return
    default_universe = trader_paper_book_default_universe(expected_currency)
    raise PersonaProvisioningCoordinationError(
        f"Approved trader paper book artifact universe {list(symbols)} does not match the "
        f"{str(expected_currency or '').strip().upper()} book universe {list(default_universe)}; "
        "an owner-approved artifact revision is required before the projection can change",
        reason_code="trader_paper_book_universe_unapproved",
    )


def _repair_runtime_binding_execution_projection_in_place(
    runtime_client: Any,
    binding: Mapping[str, Any],
    *,
    projection_metadata: Mapping[str, Any],
    tenant_id: str,
    persona_id: str,
    capital_pool_id: str,
    persona_capital_binding_id: str,
    requested_by: str,
    starting_capital: Mapping[str, Any],
    agora_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Repair an existing RuntimeBinding in place on the same binding_id via status transitions."""
    transitioner = getattr(runtime_client, "transition", None)
    if not callable(transitioner):
        raise PersonaProvisioningCoordinationError(
            "Runtime-Manager client does not support transition for in-place projection repair",
            reason_code="runtime_manager_unavailable",
        )
    binding_id = str(
        binding.get("binding_id")
        or binding.get("runtime_binding_id")
        or binding.get("id")
        or ""
    ).strip()
    existing_meta = (
        dict(binding.get("metadata") or {})
        if isinstance(binding.get("metadata"), Mapping)
        else {}
    )
    metadata_patch: Dict[str, Any] = {
        **existing_meta,
        "tenant_id": tenant_id,
        "persona_id": persona_id,
        "sponsor_persona_id": persona_id,
        "execution_context": "paper",
        "paper_only": True,
        "book_kind": "trader_servant_paper_book",
        "requested_by": str(existing_meta.get("requested_by") or requested_by),
        "starting_capital": deepcopy(
            dict(existing_meta.get("starting_capital") or starting_capital)
        ),
        **deepcopy(dict(projection_metadata)),
    }
    if agora_user_id and str(agora_user_id).strip() and not metadata_patch.get("agora_user_id"):
        metadata_patch["agora_user_id"] = str(agora_user_id).strip()

    status = str(binding.get("status") or binding.get("state") or "").strip().lower()
    if status == "active":
        transitioner(binding_id, "pending_pause", metadata_patch=metadata_patch)
        transitioner(binding_id, "paused")
    elif _is_market_admission_pause(binding):
        # The reconciler paused this binding because the market input of its
        # previous (non-executable) projection was missing; the repaired
        # projection is written while paused and the binding resumes on the
        # same binding_id. Market admission is then re-evaluated by the
        # reconciler exactly as for every other paper binding.
        transitioner(binding_id, "paused", metadata_patch=metadata_patch)
    else:
        raise PersonaProvisioningCoordinationError(
            f"RuntimeBinding {binding_id} status is {status!r}; cannot repair projection in place",
            reason_code="runtime_binding_not_active",
        )
    updated = transitioner(binding_id, "active", metadata_patch=metadata_patch)

    readback: Optional[Mapping[str, Any]] = None
    getter = getattr(runtime_client, "get", None)
    if callable(getter):
        fetched = getter(binding_id)
        if isinstance(fetched, Mapping):
            readback = fetched
    if readback is None and isinstance(updated, Mapping):
        readback = updated
    if not isinstance(readback, Mapping):
        raise PersonaProvisioningCoordinationError(
            f"Runtime-Manager did not return a readable RuntimeBinding {binding_id} after projection repair",
            reason_code="runtime_binding_readback_missing",
        )
    return _validate_runtime_binding_readback(
        readback,
        tenant_id=tenant_id,
        persona_id=persona_id,
        capital_pool_id=capital_pool_id,
        persona_capital_binding_id=persona_capital_binding_id,
    )


def _finalize_trader_paper_book_ledger_record(
    coordinator: PersonaProvisioningCoordinator,
    record: ProvisioningRecord,
    *,
    runtime_binding: Mapping[str, Any],
    ids: ProvisioningIds,
    provisioned_at: str,
) -> Optional[str]:
    """Move the book's existing ``bff.persona_provisioning`` row to a terminal state.

    The row exists only to lease the book's Registry/Governance/Deployment
    authorities. Once the book is provisioned (active, executable RuntimeBinding)
    it is recorded ``succeeded`` by the same store writer, so nothing keeps
    retrying it. No row is created when none exists, a row already recorded
    for this binding is not written again, and a retired or compensated row is
    never revived. Returns the resulting ledger state, or None without a row.
    """
    store = getattr(coordinator, "store", None)
    getter = getattr(store, "get", None)
    if not callable(getter):
        return None
    existing = getter(record.tenant_id, record.idempotency_key)
    if existing is None or not is_trader_paper_book_ledger_record(existing):
        return None
    binding_id = str(runtime_binding.get("binding_id") or "").strip()
    existing_result = existing.result if isinstance(existing.result, Mapping) else {}
    if (
        existing.state == "succeeded"
        and existing.current_step == TRADER_PAPER_BOOK_PROVISIONED_STEP
        and existing_result.get("runtime_binding_id") == binding_id
    ):
        return existing.state
    if existing.state in {"retired", "compensated"}:
        return existing.state
    leased = store.acquire(
        existing.tenant_id,
        existing.idempotency_key,
        lease_owner=coordinator.lease_owner,
        lease_seconds=coordinator.lease_seconds,
    )
    if leased is None:
        # Another coordinator holds the lease; the next ensure finalizes it.
        return existing.state
    try:
        if leased.state not in {"retired", "compensated"}:
            leased.state = "succeeded"
            leased.current_step = TRADER_PAPER_BOOK_PROVISIONED_STEP
            leased.error = None
            leased.result = {
                **(dict(leased.result) if isinstance(leased.result, Mapping) else {}),
                "status": "trader_paper_book_provisioned",
                "book_kind": "trader_servant_paper_book",
                "capital_pool_id": ids.capital_pool_id,
                "persona_capital_binding_id": ids.persona_capital_binding_id,
                "runtime_binding_id": binding_id,
                "runtime_id": str(runtime_binding.get("runtime_id") or trader_paper_runtime_id(ids)),
                "provisioned_at": provisioned_at,
            }
    finally:
        leased = store.release(
            leased,
            lease_owner=coordinator.lease_owner,
            lease_seconds=coordinator.lease_seconds,
        )
    return leased.state


def ensure_trader_paper_book(
    tenant_id: str,
    persona_id: str,
    *,
    requested_by: str,
    agora_user_id: Optional[str] = None,
    persona_record: Optional[Mapping[str, Any]] = None,
    coordinator: Optional[PersonaProvisioningCoordinator] = None,
    runtime_client: Optional[Any] = None,
    runtime_manager_client: Optional[Any] = None,
    persona_write_owner: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
    **stage_kwargs: Any,
) -> Dict[str, Any]:
    """Idempotently ensure one paper CapitalPool and one paper RuntimeBinding for a trader's servant.

    All writes go through the existing Capital and Runtime-Manager owners (plus
    Registry/Governance/Deployment when configured for Runtime-Manager authority
    verification). Repeated calls perform GET-first readbacks and never create
    duplicate pools or bindings. Canary and live stages are unconditionally
    rejected before any owner write.
    """
    clean_tenant = str(tenant_id or "").strip()
    clean_persona = str(persona_id or "").strip()
    clean_requester = str(requested_by or "").strip()
    for label, value in (
        ("tenant_id", clean_tenant),
        ("persona_id", clean_persona),
        ("requested_by", clean_requester),
    ):
        if not value:
            raise PersonaProvisioningCoordinationError(f"{label} is required")

    validate_paper_only_stage(**stage_kwargs)
    validate_servant_persona_paper_ceiling(
        persona_record,
        tenant_id=clean_tenant,
        persona_id=clean_persona,
    )

    now = _utc_now_iso()
    starting_capital = resolve_trader_paper_starting_capital(env, as_of=now)
    record = trader_paper_book_provisioning_record(
        clean_tenant,
        clean_persona,
        requested_by=clean_requester,
        starting_capital=starting_capital,
    )
    ids = deterministic_provisioning_ids(record)
    runtime_id = trader_paper_runtime_id(ids)

    from . import service as personas_service

    active_coordinator = coordinator or personas_service._chain_paper_capital_coordinator(
        clean_tenant
    )
    active_runtime_client = _build_runtime_manager_client(
        tenant_id=clean_tenant,
        actor_id=active_coordinator.actor_id,
        explicit_client=runtime_client if runtime_client is not None else runtime_manager_client,
        env=env,
    )

    pool_path = f"/api/capital-pools/{ids.capital_pool_id}"
    binding_path = f"/api/bindings/{ids.persona_capital_binding_id}"
    existing_pool = active_coordinator._owner_get("capital", pool_path)
    existing_pcb = active_coordinator._owner_get("capital", binding_path)
    pool_existed = bool(
        isinstance(existing_pool, Mapping)
        and existing_pool.get("pool_id") == ids.capital_pool_id
        and existing_pool.get("status") == "active"
    )
    pcb_existed = bool(
        isinstance(existing_pcb, Mapping)
        and existing_pcb.get("binding_id") == ids.persona_capital_binding_id
        and existing_pcb.get("status") == "active"
    )
    capital_existed = bool(pool_existed and pcb_existed)

    capital_receipt = active_coordinator.ensure_chain_paper_capital(
        tenant_id=clean_tenant,
        persona_id=clean_persona,
        capital_pool_id=ids.capital_pool_id,
        binding_id=ids.persona_capital_binding_id,
        requested_by=clean_requester,
        currency=starting_capital["currency"],
        name=f"{clean_persona} trader paper pool",
        budget=starting_capital["amount"],
        mandate="user_private_agora_servant_paper_book",
        labels={
            "task_id": "PAPER-TRADER-BOOK-PROVISION-20261127",
            "paper_only": True,
            "starting_capital_source": starting_capital["source"],
            "starting_capital_source_ref": starting_capital["source_ref"],
        },
    )
    capital_pool, risk_policy_resolution = _ensure_paper_book_risk_policy(
        active_coordinator,
        tenant_id=clean_tenant,
        persona_id=clean_persona,
        ids=ids,
        requested_by=clean_requester,
        capital_pool=capital_receipt["capital_pool"],
        persona_record=persona_record,
        persona_write_owner=persona_write_owner,
        env=env,
    )
    capital_pool.setdefault("stage", "paper")
    capital_pool.setdefault("notional_only", True)
    capital_pool["created"] = not pool_existed
    persona_capital_binding = deepcopy(dict(capital_receipt["persona_capital_binding"]))
    persona_capital_binding.setdefault("stage", "paper")
    persona_capital_binding.setdefault("notional_only", True)
    persona_capital_binding["created"] = not pcb_existed

    pool_meta = capital_pool.get("metadata")
    pool_meta_map = pool_meta if isinstance(pool_meta, Mapping) else {}
    validate_paper_only_stage(
        execution_context=str(pool_meta_map.get("execution_context") or ""),
        allowed_deployment_scope=str(persona_capital_binding.get("allowed_deployment_scope") or ""),
    )

    try:
        pool_bindings = _list_pool_runtime_bindings(active_runtime_client, ids.capital_pool_id)
    except PersonaProvisioningCoordinationError:
        raise
    except Exception as exc:
        raise PersonaProvisioningCoordinationError(
            f"Runtime-Manager owner read failed for pool {ids.capital_pool_id}: {exc}",
            reason_code="runtime_manager_unavailable",
        ) from exc

    non_terminal = [
        b
        for b in pool_bindings
        if str(b.get("status") or b.get("state") or "").strip().lower()
        not in TERMINAL_RUNTIME_BINDING_STATUSES
    ]
    for candidate in non_terminal:
        _validate_runtime_binding_readback(
            candidate,
            tenant_id=clean_tenant,
            persona_id=clean_persona,
            capital_pool_id=ids.capital_pool_id,
            persona_capital_binding_id=ids.persona_capital_binding_id,
            require_active=not _is_market_admission_pause(candidate),
        )
    if len(non_terminal) > 1:
        raise PersonaProvisioningCoordinationError(
            f"CapitalPool {ids.capital_pool_id} has {len(non_terminal)} non-terminal RuntimeBindings; "
            "single-runtime paper book invariant violated",
            reason_code="multiple_runtime_bindings",
        )

    effective_starting = _effective_starting_capital(starting_capital, capital_pool)
    runtime_binding_existed = len(non_terminal) == 1
    if runtime_binding_existed:
        runtime_binding = _validate_runtime_binding_readback(
            non_terminal[0],
            tenant_id=clean_tenant,
            persona_id=clean_persona,
            capital_pool_id=ids.capital_pool_id,
            persona_capital_binding_id=ids.persona_capital_binding_id,
            require_active=not _is_market_admission_pause(non_terminal[0]),
        )
        getter = getattr(active_runtime_client, "get", None)
        if callable(getter):
            latest = getter(runtime_binding["binding_id"])
            if isinstance(latest, Mapping):
                runtime_binding = _validate_runtime_binding_readback(
                    latest,
                    tenant_id=clean_tenant,
                    persona_id=clean_persona,
                    capital_pool_id=ids.capital_pool_id,
                    persona_capital_binding_id=ids.persona_capital_binding_id,
                    require_active=not _is_market_admission_pause(latest),
                )
        has_projection = _binding_has_executable_projection(
            runtime_binding,
            ids=ids,
            expected_currency=effective_starting["currency"],
        )
        if has_projection and _is_market_admission_pause(runtime_binding):
            # Already executable: resuming a market-admission pause belongs to
            # the paper fleet reconciler, never to ensure.
            raise PersonaProvisioningCoordinationError(
                f"RuntimeBinding {runtime_binding['binding_id']} is paused on market admission; "
                "the paper fleet reconciler resumes it on an admitted snapshot",
                reason_code="runtime_binding_not_active",
            )
        if not has_projection:
            projection_metadata = _resolve_paper_book_execution_projection(
                active_coordinator,
                record,
                ids,
                expected_currency=effective_starting["currency"],
                coordinate_authorities=True,
                env=env,
            )
            runtime_binding = _repair_runtime_binding_execution_projection_in_place(
                active_runtime_client,
                runtime_binding,
                projection_metadata=projection_metadata,
                tenant_id=clean_tenant,
                persona_id=clean_persona,
                capital_pool_id=ids.capital_pool_id,
                persona_capital_binding_id=ids.persona_capital_binding_id,
                requested_by=clean_requester,
                starting_capital=effective_starting,
                agora_user_id=agora_user_id,
            )
    else:
        projection_metadata = _resolve_paper_book_execution_projection(
            active_coordinator,
            record,
            ids,
            expected_currency=effective_starting["currency"],
            coordinate_authorities=True,
            env=env,
        )

        deploy_request: Dict[str, Any] = {
            "plan_id": ids.deployment_plan_id,
            "plan_status": "approved",
            "target_stage": "paper",
            "execution_mode": "paper",
            "artifact_id": ids.strategy_artifact_id,
            "artifact_version": ids.version,
            "strategy_id": ids.strategy_id,
            "approval_decision_id": ids.strategy_artifact_approval_decision_id,
            "sponsor_persona_id": clean_persona,
            "capital_pool_id": ids.capital_pool_id,
            "persona_capital_binding_id": ids.persona_capital_binding_id,
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "paper",
            "loader_checks_passed": True,
            "runtime_id": runtime_id,
            "metadata": {
                "tenant_id": clean_tenant,
                "persona_id": clean_persona,
                "sponsor_persona_id": clean_persona,
                "strategy_id": ids.strategy_id,
                "execution_context": "paper",
                "paper_only": True,
                "book_kind": "trader_servant_paper_book",
                "requested_by": clean_requester,
                "starting_capital": deepcopy(effective_starting),
                **deepcopy(dict(projection_metadata)),
                **(
                    {"agora_user_id": str(agora_user_id).strip()}
                    if agora_user_id and str(agora_user_id).strip()
                    else {}
                ),
            },
        }
        deployer = getattr(active_runtime_client, "deploy", None)
        if not callable(deployer):
            raise PersonaProvisioningCoordinationError(
                "Runtime-Manager client does not support deploy",
                reason_code="runtime_manager_unavailable",
            )
        deployed: Optional[Mapping[str, Any]] = None
        try:
            raw_deployed = deployer(deploy_request)
            if isinstance(raw_deployed, Mapping):
                deployed = raw_deployed
        except Exception as exc:
            # Reconcile concurrent single-runtime creation or response loss via GET readback.
            after_bindings = [
                b
                for b in _list_pool_runtime_bindings(active_runtime_client, ids.capital_pool_id)
                if str(b.get("status") or b.get("state") or "").strip().lower()
                not in TERMINAL_RUNTIME_BINDING_STATUSES
            ]
            if len(after_bindings) == 1:
                deployed = after_bindings[0]
                runtime_binding_existed = True
            else:
                raise PersonaProvisioningCoordinationError(
                    f"Runtime-Manager failed to deploy paper RuntimeBinding for pool "
                    f"{ids.capital_pool_id}: {exc}",
                    reason_code="runtime_binding_deploy_failed",
                ) from exc

        deployed_id = str(
            (deployed or {}).get("binding_id")
            or (deployed or {}).get("runtime_binding_id")
            or (deployed or {}).get("id")
            or ""
        ).strip()
        readback: Optional[Mapping[str, Any]] = None
        getter = getattr(active_runtime_client, "get", None)
        if deployed_id and callable(getter):
            fetched = getter(deployed_id)
            if isinstance(fetched, Mapping):
                readback = fetched
        if readback is None:
            after_bindings = [
                b
                for b in _list_pool_runtime_bindings(active_runtime_client, ids.capital_pool_id)
                if str(b.get("status") or b.get("state") or "").strip().lower()
                not in TERMINAL_RUNTIME_BINDING_STATUSES
            ]
            if len(after_bindings) == 1:
                readback = after_bindings[0]
            elif deployed is not None:
                readback = deployed
        if not isinstance(readback, Mapping):
            raise PersonaProvisioningCoordinationError(
                f"Runtime-Manager did not persist a readable RuntimeBinding for pool {ids.capital_pool_id}",
                reason_code="runtime_binding_readback_missing",
            )
        runtime_binding = _validate_runtime_binding_readback(
            readback,
            tenant_id=clean_tenant,
            persona_id=clean_persona,
            capital_pool_id=ids.capital_pool_id,
            persona_capital_binding_id=ids.persona_capital_binding_id,
        )
        if not _binding_has_executable_projection(
            runtime_binding,
            ids=ids,
            expected_currency=effective_starting["currency"],
        ):
            runtime_binding = _repair_runtime_binding_execution_projection_in_place(
                active_runtime_client,
                runtime_binding,
                projection_metadata=projection_metadata,
                tenant_id=clean_tenant,
                persona_id=clean_persona,
                capital_pool_id=ids.capital_pool_id,
                persona_capital_binding_id=ids.persona_capital_binding_id,
                requested_by=clean_requester,
                starting_capital=effective_starting,
                agora_user_id=agora_user_id,
            )

    runtime_binding = dict(runtime_binding)
    runtime_binding.setdefault("allowed_deployment_scope", "paper")
    runtime_binding.setdefault(
        "execution_mode",
        str(runtime_binding.get("deployment_mode") or "paper"),
    )
    runtime_binding["created"] = not runtime_binding_existed

    resolved_user_id = str(
        agora_user_id
        or ((persona_record or {}).get("metadata") or {}).get("agora_user_id")
        or (persona_record or {}).get("agora_user_id")
        or ""
    ).strip() or None

    book_summary: Dict[str, Any] = {
        "status": "provisioned",
        "tenant_id": clean_tenant,
        "agora_user_id": resolved_user_id,
        "persona_id": clean_persona,
        "book_kind": "trader_servant_paper_book",
        "stage": "paper",
        "deployment_stage": "paper",
        "environment_ceiling": "paper",
        "execution_authority": "none",
        "notional_only": True,
        "capital_pool_id": ids.capital_pool_id,
        "persona_capital_binding_id": ids.persona_capital_binding_id,
        "runtime_binding_id": runtime_binding["binding_id"],
        "runtime_id": str(runtime_binding.get("runtime_id") or runtime_id),
        "plan_id": str(runtime_binding.get("plan_id") or ids.deployment_plan_id),
        "strategy_id": ids.strategy_id,
        "strategy_artifact_id": str(runtime_binding.get("artifact_id") or ids.strategy_artifact_id),
        "strategy_artifact_version": str(runtime_binding.get("artifact_version") or ids.version),
        "starting_capital": effective_starting,
        "capital_pool": capital_pool,
        "persona_capital_binding": persona_capital_binding,
        "runtime_binding": runtime_binding,
        **(
            {"risk_policy_resolution": risk_policy_resolution}
            if risk_policy_resolution is not None
            else {}
        ),
        "idempotent_replay": bool(capital_existed and runtime_binding_existed),
        "created_capital_pool": not capital_existed,
        "created_runtime_binding": not runtime_binding_existed,
        "writer_actor_id": active_coordinator.actor_id,
        "requested_by": clean_requester,
        "provisioned_at": now,
    }

    ledger_state = _finalize_trader_paper_book_ledger_record(
        active_coordinator,
        record,
        runtime_binding=runtime_binding,
        ids=ids,
        provisioned_at=now,
    )
    if ledger_state is not None:
        book_summary["provisioning_ledger_state"] = ledger_state

    if persona_write_owner is not None:
        _persist_paper_book_metadata_on_persona(
            persona_write_owner,
            persona_id=clean_persona,
            persona_record=persona_record,
            book_summary=book_summary,
            actor_id=clean_requester,
            now=now,
        )

    return book_summary


def read_trader_paper_book(
    tenant_id: str,
    persona_id: str,
    *,
    agora_user_id: Optional[str] = None,
    persona_record: Optional[Mapping[str, Any]] = None,
    coordinator: Optional[PersonaProvisioningCoordinator] = None,
    runtime_client: Optional[Any] = None,
    runtime_manager_client: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Read-only lookup of a trader servant persona's paper book from Capital and Runtime-Manager.

    Returns ``None`` if either the CapitalPool, PersonaCapitalBinding, or active
    paper RuntimeBinding has not been provisioned yet.
    """
    clean_tenant = str(tenant_id or "").strip()
    clean_persona = str(persona_id or "").strip()
    if not clean_tenant or not clean_persona:
        raise PersonaProvisioningCoordinationError("tenant_id and persona_id are required")

    validate_servant_persona_paper_ceiling(
        persona_record,
        tenant_id=clean_tenant,
        persona_id=clean_persona,
    )

    now = _utc_now_iso()
    starting_capital = resolve_trader_paper_starting_capital(env, as_of=now)
    ids = trader_paper_book_ids(clean_tenant, clean_persona)
    runtime_id = trader_paper_runtime_id(ids)

    from . import service as personas_service

    active_coordinator = coordinator or personas_service._chain_paper_capital_coordinator(
        clean_tenant
    )
    active_runtime_client = _build_runtime_manager_client(
        tenant_id=clean_tenant,
        actor_id=active_coordinator.actor_id,
        explicit_client=runtime_client if runtime_client is not None else runtime_manager_client,
        env=env,
    )

    capital_pool = active_coordinator._owner_get(
        "capital", f"/api/capital-pools/{ids.capital_pool_id}"
    )
    if not isinstance(capital_pool, Mapping):
        return None
    if (
        capital_pool.get("pool_id") != ids.capital_pool_id
        or not active_coordinator._formal_tenant_matches(capital_pool, clean_tenant)
        or capital_pool.get("status") != "active"
        or capital_pool.get("single_runtime_enforced") is not True
        or (capital_pool.get("metadata") or {}).get("execution_context") != "paper"
        or not active_coordinator._pool_owned_by_chain(
            capital_pool.get("metadata") or {},
            persona_id=clean_persona,
            chain_id=None,
        )
    ):
        raise PersonaProvisioningCoordinationError(
            "CapitalPool readback is not an active tenant-owned paper pool for this servant persona",
            reason_code="capital_pool_invalid",
        )

    persona_capital_binding = active_coordinator._owner_get(
        "capital", f"/api/bindings/{ids.persona_capital_binding_id}"
    )
    if not isinstance(persona_capital_binding, Mapping):
        return None
    active_coordinator._check_binding_identity(
        persona_capital_binding,
        persona_id=clean_persona,
        pool_id=ids.capital_pool_id,
        binding_id=ids.persona_capital_binding_id,
    )
    if persona_capital_binding.get("status") != "active":
        return None

    pool_bindings = [
        b
        for b in _list_pool_runtime_bindings(active_runtime_client, ids.capital_pool_id)
        if str(b.get("status") or b.get("state") or "").strip().lower()
        not in TERMINAL_RUNTIME_BINDING_STATUSES
    ]
    if not pool_bindings:
        return None
    for candidate in pool_bindings:
        _validate_runtime_binding_readback(
            candidate,
            tenant_id=clean_tenant,
            persona_id=clean_persona,
            capital_pool_id=ids.capital_pool_id,
            persona_capital_binding_id=ids.persona_capital_binding_id,
        )
    if len(pool_bindings) > 1:
        raise PersonaProvisioningCoordinationError(
            f"CapitalPool {ids.capital_pool_id} has {len(pool_bindings)} non-terminal RuntimeBindings",
            reason_code="multiple_runtime_bindings",
        )
    runtime_binding = dict(pool_bindings[0])
    runtime_binding.setdefault("allowed_deployment_scope", "paper")
    runtime_binding.setdefault(
        "execution_mode",
        str(runtime_binding.get("deployment_mode") or "paper"),
    )
    runtime_binding["created"] = False
    effective_starting = _effective_starting_capital(starting_capital, capital_pool)
    resolved_user_id = str(
        agora_user_id
        or ((persona_record or {}).get("metadata") or {}).get("agora_user_id")
        or (persona_record or {}).get("agora_user_id")
        or ""
    ).strip() or None

    pool_copy = deepcopy(dict(capital_pool))
    pool_copy.setdefault("stage", "paper")
    pool_copy.setdefault("notional_only", True)
    pool_copy["created"] = False
    pcb_copy = deepcopy(dict(persona_capital_binding))
    pcb_copy.setdefault("stage", "paper")
    pcb_copy.setdefault("notional_only", True)
    pcb_copy["created"] = False

    return {
        "status": "provisioned",
        "tenant_id": clean_tenant,
        "agora_user_id": resolved_user_id,
        "persona_id": clean_persona,
        "book_kind": "trader_servant_paper_book",
        "stage": "paper",
        "deployment_stage": "paper",
        "environment_ceiling": "paper",
        "execution_authority": "none",
        "notional_only": True,
        "capital_pool_id": ids.capital_pool_id,
        "persona_capital_binding_id": ids.persona_capital_binding_id,
        "runtime_binding_id": runtime_binding["binding_id"],
        "runtime_id": str(runtime_binding.get("runtime_id") or runtime_id),
        "plan_id": str(runtime_binding.get("plan_id") or ids.deployment_plan_id),
        "strategy_id": ids.strategy_id,
        "strategy_artifact_id": str(runtime_binding.get("artifact_id") or ids.strategy_artifact_id),
        "strategy_artifact_version": str(runtime_binding.get("artifact_version") or ids.version),
        "starting_capital": effective_starting,
        "capital_pool": pool_copy,
        "persona_capital_binding": pcb_copy,
        "runtime_binding": runtime_binding,
        "idempotent_replay": True,
        "created_capital_pool": False,
        "created_runtime_binding": False,
        "writer_actor_id": active_coordinator.actor_id,
        "observed_at": now,
    }


def _persist_paper_book_metadata_on_persona(
    write_owner: Any,
    *,
    persona_id: str,
    persona_record: Optional[Mapping[str, Any]],
    book_summary: Mapping[str, Any],
    actor_id: str,
    now: str,
) -> None:
    updater = getattr(write_owner, "update_persona", None)
    if not callable(updater):
        return
    existing = dict(persona_record) if isinstance(persona_record, Mapping) else None
    if existing is None:
        getter = getattr(write_owner, "get_persona", None)
        if callable(getter):
            try:
                found = getter(persona_id)
                if isinstance(found, Mapping):
                    existing = dict(found)
            except Exception:
                existing = None
    if existing is None:
        return
    meta = dict(existing.get("metadata") or {}) if isinstance(existing.get("metadata"), Mapping) else {}
    compact_book = {
        "book_kind": "trader_servant_paper_book",
        "deployment_stage": "paper",
        "environment_ceiling": "paper",
        "execution_authority": "none",
        "capital_pool_id": book_summary["capital_pool_id"],
        "persona_capital_binding_id": book_summary["persona_capital_binding_id"],
        "runtime_binding_id": book_summary["runtime_binding_id"],
        "runtime_id": book_summary["runtime_id"],
        "plan_id": book_summary["plan_id"],
        "strategy_id": book_summary["strategy_id"],
        "strategy_artifact_id": book_summary["strategy_artifact_id"],
        "strategy_artifact_version": book_summary["strategy_artifact_version"],
        "starting_capital": deepcopy(dict(book_summary["starting_capital"])),
    }
    if (
        meta.get("paper_book") == compact_book
        and meta.get("capital_pool_id") == book_summary["capital_pool_id"]
        and meta.get("persona_capital_binding_id") == book_summary["persona_capital_binding_id"]
        and meta.get("runtime_binding_id") == book_summary["runtime_binding_id"]
        and meta.get("runtime_id") == book_summary["runtime_id"]
    ):
        return
    meta["paper_book"] = compact_book
    meta["capital_pool_id"] = book_summary["capital_pool_id"]
    meta["persona_capital_binding_id"] = book_summary["persona_capital_binding_id"]
    meta["runtime_binding_id"] = book_summary["runtime_binding_id"]
    meta["runtime_id"] = book_summary["runtime_id"]
    meta["starting_capital"] = deepcopy(dict(book_summary["starting_capital"]))
    try:
        updater(
            persona_id,
            name=str(existing.get("name") or "Agora Servant"),
            actor_id=actor_id,
            updated_at=now,
            archetype=None,
            lifecycle_state=None,
            risk_level=str(meta.get("risk_level") or existing.get("risk_level") or "low"),
            metadata=meta,
        )
    except Exception:
        pass
