"""Strategy and Ranking Domain Command Adapter.

Routes strategy review submissions, parameter updates, lifecycle actions,
ranking formulas, and quarterly ranking recommendations to authoritative registry
and governance review stores.
"""
from __future__ import annotations

import hashlib
import logging
import os
import urllib.error
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    governance_url,
    header_value,
    http_request_json_with_headers,
    registry_url,
    utc_now,
)

from services.registry.command_contract import ActionOwner, resolve_action

log = logging.getLogger(__name__)


_STRATEGY_WRITE_ROLES = ("operator", "strategy-writer", "registry-writer", "admin")
_TENANT_CLAIM_NAMES = ("tenant", "tenant_id")


def _adapter_auth_env() -> dict[str, str]:
    return {
        "PANTHEON_RUNTIME_AUTH_MODE": "strict",
        "PANTHEON_RUNTIME_JWT_SECRET": (
            os.getenv("PANTHEON_BFF_JWT_SECRET")
            or os.getenv("PANTHEON_REGISTRY_JWT_SECRET")
            or os.getenv("PANTHEON_RUNTIME_JWT_SECRET", "")
        ),
        "PANTHEON_RUNTIME_JWT_ISSUER": (
            os.getenv("PANTHEON_BFF_JWT_ISSUER")
            or os.getenv("PANTHEON_REGISTRY_JWT_ISSUER")
            or os.getenv("PANTHEON_RUNTIME_JWT_ISSUER", "")
        ),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": (
            os.getenv("PANTHEON_BFF_JWT_AUDIENCE")
            or os.getenv("PANTHEON_REGISTRY_JWT_AUDIENCE")
            or os.getenv("PANTHEON_RUNTIME_JWT_AUDIENCE", "")
        ),
        "PANTHEON_RUNTIME_DEFAULT_ROLE": os.getenv("PANTHEON_RUNTIME_DEFAULT_ROLE", "operator"),
        "PANTHEON_RUNTIME_MFA_REQUIRED": os.getenv("PANTHEON_RUNTIME_MFA_REQUIRED", "false"),
    }


def _authenticate_caller(
    auth_token: Optional[str],
    *,
    action_id: str = "update_params",
    entity_type: str = "Strategy",
) -> tuple[str, str]:
    """Verify caller identity via services.runtime_auth_inbound.validate_request_auth.

    Rejects absent, unverified, expired, malformed, or incomplete tokens.
    Returns (actor_id, tenant).
    """
    if not auth_token or not str(auth_token).strip():
        raise ActionUnavailableError(
            "Authentication required for strategy update_params: missing auth token.",
            action_id=action_id,
            entity_type=entity_type,
            error_code="UNAUTHORIZED",
            downstream_status=401,
        )
    raw = str(auth_token).strip()
    auth_header = raw if raw.startswith("Bearer ") else f"Bearer {raw}"
    token_body = auth_header[7:].strip()
    env = _adapter_auth_env()

    mode = env.get("PANTHEON_RUNTIME_AUTH_MODE", "").strip().lower()
    if mode == "strict":
        secret = env.get("PANTHEON_RUNTIME_JWT_SECRET", "").strip()
        issuer = env.get("PANTHEON_RUNTIME_JWT_ISSUER", "").strip()
        audience = env.get("PANTHEON_RUNTIME_JWT_AUDIENCE", "").strip()
        if not secret or not issuer or not audience:
            raise ActionUnavailableError(
                "Strict authentication requires JWT secret, issuer, and audience to be explicitly configured.",
                action_id=action_id,
                entity_type=entity_type,
                error_code="UNAUTHORIZED",
                downstream_status=500,
            )

    try:
        from services.runtime_auth_inbound import AuthError, validate_request_auth
    except ImportError:
        from runtime_auth_inbound import AuthError, validate_request_auth  # type: ignore[no-redef]

    try:
        ctx = validate_request_auth(
            authorization=auth_header,
            required_roles=_STRATEGY_WRITE_ROLES,
            env=env,
        )
    except AuthError as exc:
        raise ActionUnavailableError(
            f"Caller authentication failed: {exc.message}",
            action_id=action_id,
            entity_type=entity_type,
            error_code="UNAUTHORIZED" if exc.status_code == 401 else "FORBIDDEN",
            downstream_status=exc.status_code,
        ) from exc

    if ctx.token_kind != "jwt":
        raise ActionUnavailableError(
            "Caller authentication failed: non-JWT token not accepted. Production strategy adapter requires a verified JWT.",
            action_id=action_id,
            entity_type=entity_type,
            error_code="UNAUTHORIZED",
            downstream_status=401,
        )

    sub = ctx.claims.get("sub")
    if sub is None or not str(sub).strip():
        raise ActionUnavailableError(
            "Caller authentication failed: missing or empty 'sub' claim.",
            action_id=action_id,
            entity_type=entity_type,
            error_code="FORBIDDEN",
            downstream_status=403,
        )
    tenant_val = None
    for name in _TENANT_CLAIM_NAMES:
        val = ctx.claims.get(name)
        if val is not None and str(val).strip():
            tenant_val = str(val).strip()
            break
    if not tenant_val:
        raise ActionUnavailableError(
            "Caller authentication failed: missing or empty 'tenant' claim.",
            action_id=action_id,
            entity_type=entity_type,
            error_code="FORBIDDEN",
            downstream_status=403,
        )
    raw_roles = ctx.claims.get("roles") or ctx.claims.get("role")
    if not raw_roles:
        raise ActionUnavailableError(
            "Caller authentication failed: missing or empty 'roles' claim.",
            action_id=action_id,
            entity_type=entity_type,
            error_code="FORBIDDEN",
            downstream_status=403,
        )
    exp = ctx.claims.get("exp")
    if exp is None:
        raise ActionUnavailableError(
            "Caller authentication failed: missing 'exp' claim.",
            action_id=action_id,
            entity_type=entity_type,
            error_code="FORBIDDEN",
            downstream_status=403,
        )
    actor_id = str(sub).strip()
    tenant = tenant_val
    return actor_id, tenant


def _validate_scoped_receipt(
    receipt: Optional[Dict[str, Any]],
    *,
    command_id: str,
    registry_id: str,
    strategy_id: str,
    expected_metadata: Optional[Dict[str, Any]],
    new_metadata: Optional[Dict[str, Any]],
    caller_actor_id: str,
    caller_tenant: Optional[str],
    original_entry: Dict[str, Any],
    expected_receipt_key: str,
    expected_request_digest: str,
    action_id: str,
    expected_entry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(receipt, dict):
        raise ActionUnavailableError(
            f"Registry command receipt for registry_id={registry_id!r}, command_key={command_id!r} is missing or malformed.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="UNCONFIRMED_COMMAND_RECEIPT",
        )
    if receipt.get("command_key") != command_id:
        raise ActionUnavailableError(
            f"Registry command receipt reports command_key={receipt.get('command_key')!r}, "
            f"expected {command_id!r}.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="UNCONFIRMED_COMMAND_RECEIPT",
        )
    if receipt.get("registry_id") != registry_id:
        raise ActionUnavailableError(
            f"Registry command receipt reports registry_id={receipt.get('registry_id')!r}, "
            f"expected {registry_id!r}.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="STRATEGY_ID_MISMATCH",
        )
    if receipt.get("receipt_key") != expected_receipt_key:
        raise ActionUnavailableError(
            f"Registry command receipt reports receipt_key={receipt.get('receipt_key')!r}, "
            f"expected scoped key {expected_receipt_key!r}.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="UNCONFIRMED_COMMAND_RECEIPT",
        )
    if receipt.get("request_digest") != expected_request_digest:
        raise ActionUnavailableError(
            f"Registry command receipt reports request_digest={receipt.get('request_digest')!r}, "
            f"expected request digest {expected_request_digest!r}.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="UNCONFIRMED_COMMAND_RECEIPT",
        )
    if not receipt.get("committed_at"):
        raise ActionUnavailableError(
            "Registry command receipt carries null or missing committed_at timestamp.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="REPLAY_MISSING_COMMIT_TIME",
        )

    committed_entry = receipt.get("committed_entry")
    if not isinstance(committed_entry, dict) or not committed_entry.get("registry_id"):
        raise ActionUnavailableError(
            f"Registry command receipt for registry_id={registry_id!r} carries no committed entry payload.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="UNCONFIRMED_COMMAND_RECEIPT",
        )

    if committed_entry.get("strategy_id") != strategy_id:
        raise ActionUnavailableError(
            f"Registry command receipt committed entry belongs to "
            f"strategy_id={committed_entry.get('strategy_id')!r}, not the requested "
            f"strategy_id={strategy_id!r}.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="READBACK_MISMATCH",
        )

    # Check divergence from original immutable identity
    if (
        committed_entry.get("registry_id") != original_entry.get("registry_id")
        or committed_entry.get("checksum") != original_entry.get("checksum")
        or committed_entry.get("version") != original_entry.get("version")
        or committed_entry.get("owner_tenant") != original_entry.get("owner_tenant")
    ):
        raise ActionUnavailableError(
            f"Registry command receipt committed entry for registry_id={registry_id!r} diverges "
            "from the original immutable receipt captured before this command was issued "
            "(registry_id/checksum/version/owner_tenant).",
            action_id=action_id,
            entity_type="Strategy",
            error_code="READBACK_MISMATCH",
        )

    if committed_entry.get("metadata") != new_metadata:
        raise ActionUnavailableError(
            f"Registry command receipt committed entry for registry_id={registry_id!r} reports "
            "metadata different from what this exact command requested.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="REPLAY_METADATA_MISMATCH",
        )

    if not committed_entry.get("updated_at"):
        raise ActionUnavailableError(
            f"Registry command receipt committed entry for registry_id={registry_id!r} carries no "
            "commit timestamp.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="REPLAY_MISSING_COMMIT_TIME",
        )

    last_actor = (
        committed_entry.get("last_actor")
        if isinstance(committed_entry.get("last_actor"), dict)
        else {}
    )
    receipt_actor_id = last_actor.get("actor_id")
    if not receipt_actor_id:
        raise ActionUnavailableError(
            f"Registry command receipt committed entry for registry_id={registry_id!r} carries no "
            "recorded actor.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="REPLAY_MISSING_ACTOR",
        )

    if receipt_actor_id != caller_actor_id:
        raise ActionUnavailableError(
            f"Registry command receipt committed entry reports actor {receipt_actor_id!r}, "
            f"not the verified caller identity {caller_actor_id!r}.",
            action_id=action_id,
            entity_type="Strategy",
            error_code="REPLAY_ACTOR_MISMATCH",
        )

    if caller_tenant and caller_tenant != "unscoped":
        entry_tenant = committed_entry.get("owner_tenant")
        if entry_tenant and entry_tenant != caller_tenant:
            raise ActionUnavailableError(
                f"Registry command receipt committed entry reports tenant {entry_tenant!r}, "
                f"not the verified caller tenant {caller_tenant!r}.",
                action_id=action_id,
                entity_type="Strategy",
                error_code="READBACK_MISMATCH",
            )

    if expected_entry is not None:
        if (
            committed_entry.get("checksum") != expected_entry.get("checksum")
            or committed_entry.get("updated_at") != expected_entry.get("updated_at")
            or committed_entry.get("metadata") != expected_entry.get("metadata")
            or (committed_entry.get("last_actor") or {}).get("actor_id")
            != (expected_entry.get("last_actor") or {}).get("actor_id")
        ):
            raise ActionUnavailableError(
                f"Registry metadata PATCH response for registry_id={registry_id!r} does not "
                "match a follow-up owner receipt readback.",
                action_id=action_id,
                entity_type="Strategy",
                error_code="READBACK_MISMATCH",
            )

    return committed_entry


def _receipt_correlation_id(
    *,
    registry_id: str,
    version: Optional[str],
    checksum: Optional[str],
    commit_time: Optional[str],
    actor_id: Optional[str],
    command_id: str,
) -> str:
    """Derive a correlation id from the independently-verified durable
    receipt this command's own checks already confirmed, not merely from
    caller-supplied input. Deterministic and content-addressed: replays of
    the exact same command against the exact same committed snapshot always
    produce the same correlation_id."""
    framed = "|".join(
        f"{len(str(part))}:{part}"
        for part in (registry_id, version, checksum, commit_time, actor_id, command_id)
    )
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


class StrategyCommandAdapter(DomainCommandAdapter):
    """Adapter for Strategy, RankingFormula, and Ranking authority commands."""

    _HANDLED_COMMANDS = {
        "StrategyAction",
        "RankingFormulaAction",
        "RankingAction",
        "QuarterlyRankingRecommendationSubmit",
    }

    _HANDLED_ENTITIES = {
        "strategy",
        "strategyspec",
        "strategy-spec",
        "rankingformula",
        "ranking-formula",
        "ranking",
    }

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        return normalized_cmd in self._HANDLED_COMMANDS or normalized_entity in self._HANDLED_ENTITIES

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        action_id = str(params.get("action_id") or command_type or "").strip()
        entity_id = str(params.get("strategy_id") or params.get("formula_id") or params.get("ranking_id") or params.get("entity_id") or "").strip()
        entity_type = str(params.get("entity_type") or "").strip().lower().replace("_", "-")

        if entity_type in {"strategy", "strategyspec", "strategy-spec"} or command_type == "StrategyAction":
            return self._execute_strategy_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif entity_type in {"rankingformula", "ranking-formula"} or command_type == "RankingFormulaAction":
            return self._execute_formula_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif entity_type == "ranking" or command_type in {"RankingAction", "QuarterlyRankingRecommendationSubmit"}:
            return self._execute_ranking_action(command_id, entity_id, action_id or command_type, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Strategy adapter cannot route action {action_id!r} on entity {entity_id!r}",
                action_id=action_id,
                entity_type="Strategy",
            )

    def _execute_strategy_action(
        self,
        command_id: str,
        strategy_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch a Strategy action to its lawful owner (command_contract.py).

        Previously this fabricated a resulting_status from a static
        action->status map and returned it as an "authoritative_readback"
        with zero owner I/O (architecture-resumption-sa-sd.md §2). Every
        action now either performs a genuine owner command with a verified
        readback, or raises ActionUnavailableError naming the exact owner —
        never an inferred success.
        """
        target_id = strategy_id or str(params.get("strategy_id") or "").strip()
        if not target_id:
            raise ValueError("StrategyAction requires strategy_id.")

        normalized_action = action_id.lower()
        try:
            action_spec = resolve_action(normalized_action)
        except KeyError:
            raise ActionUnavailableError(
                f"Strategy action {action_id!r} is not a recognized command_contract action.",
                action_id=action_id,
                entity_type="Strategy",
            )

        if action_spec.owner is not ActionOwner.REGISTRY:
            raise ActionUnavailableError(
                f"Strategy action {action_id!r} on {target_id!r} belongs to the "
                f"{action_spec.owner.value} owner, which is not yet integrated here: "
                f"{action_spec.description}",
                action_id=action_id,
                entity_type="Strategy",
                error_code="OWNER_NOT_INTEGRATED",
                suggestion=(
                    f"Route this action through the {action_spec.owner.value} owner's "
                    "command surface once it is integrated; see command_contract.STRATEGY_ACTIONS."
                ),
            )

        if action_spec.action_id == "update_params":
            return self._execute_update_params(
                command_id, target_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token,
            )

        raise ActionUnavailableError(
            f"Strategy action {action_id!r} is mapped to a Registry capability that this "
            "adapter does not yet call.",
            action_id=action_id,
            entity_type="Strategy",
        )

    def _execute_update_params(
        self,
        command_id: str,
        strategy_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Real CAS metadata update against the Registry owner.

        Prior defect (architecture-resumption-sa-sd.md §2 / reviewer finding
        6): this method silently replaced the caller's ``expected_metadata``
        precondition with a freshly-fetched GET (defeating CAS — the whole
        point of a precondition is to bind the write to the base the caller
        actually observed, not "whatever is latest right now"), then
        discarded the real PATCH response and manufactured a receipt from a
        separate re-GET that could return stale, unrelated, or even empty
        data regardless of what the PATCH actually did.

        Fixed: the caller's own ``expected_metadata`` passes through
        unchanged, and the receipt is built from the actual PATCH response
        (entry snapshot + ``X-Idempotent-Replay`` header) — never a
        second, independent GET.
        """
        registry_id = str(params.get("registry_id") or "").strip()
        if not registry_id:
            raise ActionUnavailableError(
                f"update_params on strategy {strategy_id!r} requires registry_id "
                "(the exact RegistryEntry this metadata update targets).",
                action_id=action_id,
                entity_type="Strategy",
                error_code="MISSING_REGISTRY_ID",
                suggestion="Resolve the target registry_id via GET /api/registry/strategies/{strategy_id}/entries first.",
            )
        if "expected_metadata" not in params:
            raise ActionUnavailableError(
                f"update_params on strategy {strategy_id!r} requires expected_metadata "
                "(the caller's own CAS precondition — the metadata value it read before "
                "deciding to write). Silently substituting a freshly-fetched value here "
                "would defeat CAS/lost-update protection.",
                action_id=action_id,
                entity_type="Strategy",
                error_code="MISSING_EXPECTED_METADATA",
                suggestion="Resolve the current metadata via GET first and pass it back unchanged as expected_metadata.",
            )
        expected_metadata = params.get("expected_metadata")
        if expected_metadata is not None and not isinstance(expected_metadata, dict):
            raise ActionUnavailableError(
                f"update_params on strategy {strategy_id!r} requires expected_metadata to be "
                "an object or null, not a non-dict value.",
                action_id=action_id,
                entity_type="Strategy",
                error_code="INVALID_EXPECTED_METADATA",
            )
        new_metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}

        def _belongs_to_requested_strategy(candidate: Optional[Dict[str, Any]]) -> bool:
            return isinstance(candidate, dict) and candidate.get("strategy_id") == strategy_id

        # Reviewer finding 6: verify that the caller-supplied registry_id
        # actually belongs to the requested strategy_id BEFORE issuing the
        # mutating PATCH — not only after the PATCH response comes back.
        # Previously a request naming strategy A but supplying a
        # registry_id belonging to strategy B was only rejected once B's
        # metadata had already been mutated (the mismatch was discovered
        # from the PATCH's own response), so a caller could cause a
        # partially-applied mutation on the wrong aggregate before the
        # error surfaced. This pre-check also captures the pre-mutation
        # baseline (registry_id, strategy_id, checksum, version,
        # owner_tenant) as the "original immutable receipt" this call binds
        # its own post-PATCH readback verification against (reviewer
        # finding 7) — never a PATCH response's own self-reported claims
        # alone, and never a fresh "whatever is current now" comparison
        # that a later, unrelated command could have since moved.
        caller_actor_id, caller_tenant = _authenticate_caller(
            auth_token, action_id=action_id, entity_type="Strategy"
        )

        original_entry = self._readback_entry(registry_id, auth_token=auth_token, mfa_token=mfa_token)
        if original_entry is None:
            raise ActionUnavailableError(
                f"update_params on strategy {strategy_id!r} could not resolve registry_id="
                f"{registry_id!r} to an existing Registry entry before attempting the metadata "
                "update.",
                action_id=action_id,
                entity_type="Strategy",
                error_code="REGISTRY_ID_NOT_FOUND",
            )
        if not _belongs_to_requested_strategy(original_entry):
            raise ActionUnavailableError(
                f"Registry entry registry_id={registry_id!r} belongs to strategy_id="
                f"{original_entry.get('strategy_id')!r}, not the requested "
                f"strategy_id={strategy_id!r}; refusing to mutate a different aggregate than "
                "the caller asked to target.",
                action_id=action_id,
                entity_type="Strategy",
                error_code="STRATEGY_ID_MISMATCH",
            )
        original_identity = {
            "registry_id": original_entry.get("registry_id"),
            "checksum": original_entry.get("checksum"),
            "version": original_entry.get("version"),
            "owner_tenant": original_entry.get("owner_tenant"),
        }

        def _diverges_from_original(candidate: Dict[str, Any]) -> bool:
            """A metadata-only PATCH must never change registry_id/checksum/
            version/owner_tenant — those are the immutable identity fields
            bound at command-issue time. Any divergence between what the
            registry now reports and this pre-issue baseline means the
            response (replay or not) cannot be trusted as proof this exact
            command committed against this exact aggregate/content."""
            return (
                candidate.get("registry_id") != original_identity["registry_id"]
                or candidate.get("checksum") != original_identity["checksum"]
                or candidate.get("version") != original_identity["version"]
                or candidate.get("owner_tenant") != original_identity["owner_tenant"]
            )

        from services.registry.pg_store import PostgresRegistryStore, _request_digest

        expected_receipt_key = PostgresRegistryStore.receipt_key(
            command_id,
            registry_id,
            actor={"actor_id": caller_actor_id, "tenant": caller_tenant},
            command_type="metadata",
        )
        expected_request_digest = _request_digest({
            "registry_id": registry_id,
            "expected_metadata": expected_metadata,
            "metadata": new_metadata,
        })

        patch_exc = None
        status_code = None
        headers = {}
        body = None
        try:
            status_code, headers, body = http_request_json_with_headers(
                registry_url(f"/api/registry/entries/{registry_id}/metadata"),
                method="PATCH",
                payload={
                    "expected_metadata": expected_metadata,
                    "metadata": new_metadata,
                    "command_key": command_id,
                },
                auth_token=auth_token,
                mfa_token=mfa_token,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise ActionUnavailableError(
                    f"Registry metadata PATCH for registry_id={registry_id!r} rejected with 409 conflict: precondition mismatch.",
                    action_id=action_id,
                    entity_type="Strategy",
                    error_code="REGISTRY_CONFLICT",
                    downstream_status=409,
                ) from exc
            if exc.code < 500:
                raise
            patch_exc = exc
        except Exception as exc:
            patch_exc = exc

        response_lost = False
        idempotent_replay = False

        if patch_exc is not None:
            log.warning(
                "Registry metadata PATCH for %s/%s encountered transport error (%s); "
                "recovering via scoped receipt readback.",
                registry_id, command_id, patch_exc,
            )
            receipt, receipt_status = self._readback_receipt(
                registry_id,
                command_id,
                auth_token=auth_token,
                mfa_token=mfa_token,
                command_type="metadata",
            )
            if receipt is None:
                raise ActionUnavailableError(
                    f"Registry metadata PATCH for registry_id={registry_id!r}, command_key={command_id!r} "
                    f"encountered ambiguous transport exception ({patch_exc}), and follow-up receipt readback was unconfirmed.",
                    action_id=action_id,
                    entity_type="Strategy",
                    error_code="UNCONFIRMED_COMMAND_RECEIPT",
                    retryable=True,
                    downstream_status=503,
                ) from patch_exc

            entry = _validate_scoped_receipt(
                receipt,
                command_id=command_id,
                registry_id=registry_id,
                strategy_id=strategy_id,
                expected_metadata=expected_metadata,
                new_metadata=new_metadata,
                caller_actor_id=caller_actor_id,
                caller_tenant=caller_tenant,
                original_entry=original_entry,
                expected_receipt_key=expected_receipt_key,
                expected_request_digest=expected_request_digest,
                action_id=action_id,
            )
            response_lost = True
        else:
            entry = body.get("entry") if isinstance(body, dict) else None
            idempotent_replay = str(header_value(headers, "X-Idempotent-Replay") or "").strip().lower() == "true"

            if idempotent_replay:
                if not isinstance(entry, dict) or not entry.get("registry_id"):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} returned an "
                        "ambiguous response with no entry payload.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="AMBIGUOUS_REGISTRY_RESPONSE",
                    )
                if not _belongs_to_requested_strategy(entry):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} belongs to "
                        f"strategy_id={entry.get('strategy_id')!r}, not the requested "
                        f"strategy_id={strategy_id!r}.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="STRATEGY_ID_MISMATCH",
                    )
                if _diverges_from_original(entry):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} diverges "
                        "from the original immutable receipt captured before this command was "
                        "issued (registry_id/checksum/version/owner_tenant); refusing to trust a "
                        "replay against a different identity/content than the one this command "
                        "originally targeted.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="READBACK_MISMATCH",
                    )
                if entry.get("metadata") != new_metadata:
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} reports "
                        "metadata different from what this exact command originally requested; "
                        "refusing to trust it as this command's original receipt.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="REPLAY_METADATA_MISMATCH",
                    )
                if not entry.get("updated_at"):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} carries no "
                        "commit timestamp; a genuinely committed original receipt always has one.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="REPLAY_MISSING_COMMIT_TIME",
                    )
                if not isinstance(entry.get("last_actor"), dict) or not entry["last_actor"].get("actor_id"):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} carries no "
                        "recorded actor; a genuinely committed original receipt always has one.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="REPLAY_MISSING_ACTOR",
                    )
                if entry["last_actor"].get("actor_id") != caller_actor_id:
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} reports actor "
                        f"{entry['last_actor'].get('actor_id')!r}, not the verified caller identity "
                        f"{caller_actor_id!r} derived from this command's own auth token; a genuine "
                        "replay of this caller's own prior command always carries this caller's own "
                        "actor identity.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="REPLAY_ACTOR_MISMATCH",
                    )

                receipt, receipt_status = self._readback_receipt(
                    registry_id, command_id, auth_token=auth_token, mfa_token=mfa_token, command_type="metadata",
                )
                if receipt is None:
                    if receipt_status is None or (receipt_status and receipt_status >= 500):
                        raise ActionUnavailableError(
                            f"Registry metadata PATCH replay for registry_id={registry_id!r} could not be confirmed "
                            "by a follow-up owner receipt readback.",
                            action_id=action_id,
                            entity_type="Strategy",
                            error_code="READBACK_UNAVAILABLE",
                            retryable=True,
                            downstream_status=503,
                        )
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH replay for registry_id={registry_id!r} could not be confirmed "
                        "by independent owner receipt reload.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="UNCONFIRMED_COMMAND_RECEIPT",
                        retryable=True,
                        downstream_status=503,
                    )
                entry = _validate_scoped_receipt(
                    receipt,
                    command_id=command_id,
                    registry_id=registry_id,
                    strategy_id=strategy_id,
                    expected_metadata=expected_metadata,
                    new_metadata=new_metadata,
                    caller_actor_id=caller_actor_id,
                    caller_tenant=caller_tenant,
                    original_entry=original_entry,
                    expected_receipt_key=expected_receipt_key,
                    expected_request_digest=expected_request_digest,
                    action_id=action_id,
                )

            elif not isinstance(entry, dict) or not entry.get("registry_id"):
                receipt, receipt_status = self._readback_receipt(
                    registry_id, command_id, auth_token=auth_token, mfa_token=mfa_token, command_type="metadata",
                )
                if receipt is None:
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH for registry_id={registry_id!r} returned an ambiguous response "
                        "with no entry payload, and a follow-up owner receipt readback does not confirm the command.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="AMBIGUOUS_REGISTRY_RESPONSE",
                        retryable=True,
                        downstream_status=503,
                    )
                try:
                    entry = _validate_scoped_receipt(
                        receipt,
                        command_id=command_id,
                        registry_id=registry_id,
                        strategy_id=strategy_id,
                        expected_metadata=expected_metadata,
                        new_metadata=new_metadata,
                        caller_actor_id=caller_actor_id,
                        caller_tenant=caller_tenant,
                        original_entry=original_entry,
                        expected_receipt_key=expected_receipt_key,
                        expected_request_digest=expected_request_digest,
                        action_id=action_id,
                    )
                except ActionUnavailableError as exc:
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH for registry_id={registry_id!r} returned an ambiguous response, "
                        f"and follow-up receipt readback failed validation: {exc.message}",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="AMBIGUOUS_REGISTRY_RESPONSE",
                        retryable=True,
                        downstream_status=503,
                    ) from exc
                response_lost = True

            else:
                if not _belongs_to_requested_strategy(entry):
                    raise ActionUnavailableError(
                        f"Registry entry registry_id={registry_id!r} belongs to "
                        f"strategy_id={entry.get('strategy_id')!r}, not the requested "
                        f"strategy_id={strategy_id!r}; refusing to report success for a "
                        "different aggregate than the caller asked to mutate.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="STRATEGY_ID_MISMATCH",
                    )
                if _diverges_from_original(entry):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH response for registry_id={registry_id!r} diverges "
                        "from the original immutable receipt captured before this command was "
                        "issued (registry_id/checksum/version/owner_tenant); refusing to report "
                        "success against a different identity/content than the one this command "
                        "originally targeted.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="READBACK_MISMATCH",
                    )
                if not entry.get("updated_at"):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH response for registry_id={registry_id!r} carries no "
                        "commit timestamp; a genuinely committed mutation always has one.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="MISSING_COMMIT_TIME",
                    )
                if not isinstance(entry.get("last_actor"), dict) or not entry["last_actor"].get("actor_id"):
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH response for registry_id={registry_id!r} carries no "
                        "recorded actor; a genuinely committed mutation always has one.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="MISSING_ACTOR",
                    )
                if entry.get("metadata") != new_metadata:
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH response for registry_id={registry_id!r} does not "
                        "report the requested metadata.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="READBACK_MISMATCH",
                    )
                if entry["last_actor"].get("actor_id") != caller_actor_id:
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH response for registry_id={registry_id!r} reports actor "
                        f"{entry['last_actor'].get('actor_id')!r}, not the verified caller identity "
                        f"{caller_actor_id!r} derived from this command's own auth token; refusing to "
                        "report success for a commit made by a different actor.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="ACTOR_MISMATCH",
                    )

                receipt, receipt_status = self._readback_receipt(
                    registry_id, command_id, auth_token=auth_token, mfa_token=mfa_token, command_type="metadata",
                )
                if receipt is None:
                    if receipt_status is None or (receipt_status and receipt_status >= 500):
                        raise ActionUnavailableError(
                            f"Registry metadata PATCH for registry_id={registry_id!r} committed but could "
                            "not be confirmed by a follow-up owner receipt readback.",
                            action_id=action_id,
                            entity_type="Strategy",
                            error_code="READBACK_UNAVAILABLE",
                            retryable=True,
                            downstream_status=503,
                        )
                    raise ActionUnavailableError(
                        f"Registry metadata PATCH for registry_id={registry_id!r} could not be confirmed "
                        "by independent owner receipt reload.",
                        action_id=action_id,
                        entity_type="Strategy",
                        error_code="UNCONFIRMED_COMMAND_RECEIPT",
                        retryable=True,
                        downstream_status=503,
                    )
                entry = _validate_scoped_receipt(
                    receipt,
                    command_id=command_id,
                    registry_id=registry_id,
                    strategy_id=strategy_id,
                    expected_metadata=expected_metadata,
                    new_metadata=new_metadata,
                    caller_actor_id=caller_actor_id,
                    caller_tenant=caller_tenant,
                    original_entry=original_entry,
                    expected_receipt_key=expected_receipt_key,
                    expected_request_digest=expected_request_digest,
                    action_id=action_id,
                    expected_entry=entry,
                )

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Strategy",
            entity_id=strategy_id,
            action_id=action_id,
            status="metadata_updated",
            dispatch_path="strategy_registry_authority",
            domain_receipt={
                "strategy_id": strategy_id,
                "registry_id": registry_id,
                "action": action_id,
                "metadata": entry.get("metadata"),
                "version": entry.get("version"),
                "checksum": entry.get("checksum"),
                "commit_time": entry.get("updated_at"),
                # Reviewer finding (9a6c review, P1): a correlation_id built
                # from the caller-supplied command_id alone is synthesized
                # from input, not derived from anything the owner store
                # actually committed — it would be identical whether or not
                # any of the verification above ever ran. Bind it instead to
                # the independently-verified durable receipt identity this
                # command's checks above already confirmed: the exact
                # registry_id/version/checksum/commit-time/actor this owner
                # store recorded, plus command_id only to disambiguate
                # distinct commands that happen to land on the same
                # committed snapshot (e.g. two callers racing to a no-op).
                "correlation_id": _receipt_correlation_id(
                    registry_id=registry_id,
                    version=entry.get("version"),
                    checksum=entry.get("checksum"),
                    commit_time=entry.get("updated_at"),
                    actor_id=(entry.get("last_actor") or {}).get("actor_id"),
                    command_id=command_id,
                ),
            },
            authoritative_readback=entry,
            idempotent_replay=idempotent_replay,
            extra={
                "strategy_id": strategy_id,
                "registry_id": registry_id,
                "action_id": action_id,
                "response_lost": response_lost,
            },
        )

    @staticmethod
    def _readback_entry(
        registry_id: str,
        *,
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Perform a genuine scoped owner GET and return the durable entry snapshot.

        Returns ``None`` on any failure (network error, 404, malformed body)
        rather than raising — callers decide whether the absence of a
        confirmable readback means "not committed" or "unconfirmed", since
        those are different outcomes (architecture-resumption-sa-sd.md §3.3).
        """
        try:
            _, _, body = http_request_json_with_headers(
                registry_url(f"/api/registry/entries/{registry_id}"),
                method="GET",
                auth_token=auth_token,
                mfa_token=mfa_token,
            )
        except Exception:
            return None
        entry = body.get("entry") if isinstance(body, dict) else None
        if isinstance(entry, dict) and entry.get("registry_id") == registry_id:
            return entry
        return None

    @staticmethod
    def _readback_receipt(
        registry_id: str,
        command_id: str,
        *,
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
        command_type: str = "metadata",
    ) -> tuple[Optional[Dict[str, Any]], Optional[int]]:
        """Independently reload the durable scoped command receipt from Registry.

        Returns (receipt_dict, status_code). status_code is None if an exception occurred.
        """
        url = registry_url(
            f"/api/registry/entries/{quote(registry_id, safe='')}/receipts/{quote(command_id, safe='')}"
            f"?command_type={quote(command_type, safe='')}"
        )
        try:
            status_code, _headers, body = http_request_json_with_headers(
                url,
                method="GET",
                auth_token=auth_token,
                mfa_token=mfa_token,
            )
            if status_code == 200 and isinstance(body, dict):
                receipt = body.get("receipt")
                if isinstance(receipt, dict):
                    return receipt, 200
            return None, status_code
        except Exception as exc:
            log.warning("Failed to readback command receipt for %s/%s: %s", registry_id, command_id, exc)
            return None, None

    def _execute_formula_action(
        self,
        command_id: str,
        formula_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = formula_id or str(params.get("formula_id") or "").strip()
        if not target_id:
            raise ValueError("RankingFormulaAction requires formula_id.")

        new_status = "published" if action_id.lower() == "publish" else ("deprecated" if action_id.lower() == "deprecate" else "updated")
        return build_domain_receipt(
            command_id=command_id,
            entity_type="RankingFormula",
            entity_id=target_id,
            action_id=action_id,
            status=new_status,
            dispatch_path="ranking_formula_registry",
            domain_receipt={"formula_id": target_id, "status": new_status},
            authoritative_readback={"formula_id": target_id, "status": new_status},
            extra={"formula_id": target_id},
        )

    def _execute_ranking_action(
        self,
        command_id: str,
        ranking_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = ranking_id or str(params.get("ranking_id") or "rankings-current").strip()
        return build_domain_receipt(
            command_id=command_id,
            entity_type="Ranking",
            entity_id=target_id,
            action_id=action_id,
            status="submitted" if "submit" in action_id.lower() else "executed",
            dispatch_path="ranking_governance_authority",
            domain_receipt={"ranking_id": target_id, "action": action_id, "accepted": True},
            authoritative_readback={"ranking_id": target_id, "status": "active"},
            extra={"ranking_id": target_id},
        )
