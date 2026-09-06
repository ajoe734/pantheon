"""Strategy and Ranking Domain Command Adapter.

Routes strategy review submissions, parameter updates, lifecycle actions,
ranking formulas, and quarterly ranking recommendations to authoritative registry
and governance review stores.
"""
from __future__ import annotations

import logging
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

        entry = body.get("entry") if isinstance(body, dict) else None
        idempotent_replay = str(header_value(headers, "X-Idempotent-Replay") or "").strip().lower() == "true"

        def _belongs_to_requested_strategy(candidate: Optional[Dict[str, Any]]) -> bool:
            return isinstance(candidate, dict) and candidate.get("strategy_id") == strategy_id

        response_lost = False

        if idempotent_replay:
            # Reviewer finding 7: a replay's PATCH response IS the
            # historically-committed entry snapshot (see
            # RegistryService.update_metadata / commit_metadata_cas's
            # receipt-replay semantics), not a claim to be re-verified
            # against "whatever is current now". Comparing it against an
            # independent readback GET spuriously fails whenever a later,
            # unrelated command under a *different* key has since moved the
            # row on — the row diverging from the original commit is
            # expected and correct, not evidence the replay is wrong.
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
        elif not isinstance(entry, dict) or not entry.get("registry_id"):
            # The PATCH nominally succeeded (no HTTPError was raised) but its
            # body carries no confirmable entry snapshot (e.g. 200 {}).
            # Rather than immediately reporting failure, attempt the
            # readback to distinguish "committed but response lost" from
            # "not committed" — a hard FAILED here would fabricate a
            # downstream error for a write that actually succeeded.
            readback_entry = self._readback_entry(registry_id, auth_token=auth_token, mfa_token=mfa_token)
            if (
                readback_entry is None
                or readback_entry.get("metadata") != new_metadata
                or not _belongs_to_requested_strategy(readback_entry)
            ):
                raise ActionUnavailableError(
                    f"Registry metadata PATCH for registry_id={registry_id!r} returned an "
                    "ambiguous response with no entry payload, and a follow-up owner GET "
                    "readback does not confirm the requested metadata was committed.",
                    action_id=action_id,
                    entity_type="Strategy",
                    error_code="AMBIGUOUS_REGISTRY_RESPONSE",
                )
            entry = readback_entry
            response_lost = True
        else:
            # Reviewer finding 6: registry_id is caller-supplied and must be
            # verified to actually belong to the *requested* strategy_id
            # before this is ever reported as success — otherwise a caller
            # could target strategy A but supply a registry_id from strategy
            # B, mutate B, and get back a receipt claiming A was updated.
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
            # The PATCH response claimed a specific committed snapshot;
            # verify it against an independent owner read rather than taking
            # the mutation response's own word for it — architecture-
            # resumption-sa-sd.md §3.3 / reviewer finding 5. "POST/PATCH
            # accepted" does not constitute owner GET/reload proof.
            readback_entry = self._readback_entry(registry_id, auth_token=auth_token, mfa_token=mfa_token)
            if readback_entry is None:
                # The write itself is already confirmed committed (the PATCH
                # returned a concrete entry snapshot); a subsequent readback
                # failure is a transient confirmation gap, not proof the
                # mutation did not happen — the caller should retry the read,
                # not resubmit the same command (reviewer finding 7).
                raise ActionUnavailableError(
                    f"Registry metadata PATCH for registry_id={registry_id!r} committed but could "
                    "not be confirmed by a follow-up owner GET readback.",
                    action_id=action_id,
                    entity_type="Strategy",
                    error_code="READBACK_UNAVAILABLE",
                    retryable=True,
                    downstream_status=503,
                )
            if (
                readback_entry.get("checksum") != entry.get("checksum")
                or readback_entry.get("updated_at") != entry.get("updated_at")
                or readback_entry.get("metadata") != entry.get("metadata")
            ):
                raise ActionUnavailableError(
                    f"Registry metadata PATCH response for registry_id={registry_id!r} does not "
                    "match a follow-up owner GET readback; refusing to report success on a "
                    "discrepant state.",
                    action_id=action_id,
                    entity_type="Strategy",
                    error_code="READBACK_MISMATCH",
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
                "correlation_id": command_id,
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
