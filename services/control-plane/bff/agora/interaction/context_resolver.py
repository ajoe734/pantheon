"""Agora interaction context reference resolver.

Decoupled from composition root globals. Accepts explicit read_store,
identity extractors, role verifiers, schema paths, and filter callbacks.

Canonical seam per
``docs/operations/bff-test-migration-b05-journal-context-resolver-seam.md``
(BFF-TEST-MIGRATION-B05-JOURNAL-CONTEXT-RESOLVER-SEAM-DECISION-001,
BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001): single ACL owner for the
interaction context kinds whose existing owner can prove audience scope
(``decision_event`` and ``journal_entry``). ``position``,
``performance_window``, and ``human_inbox_item`` remain explicit
dependency-unavailable sources -- they have no canonical per-user ownership
contract yet. DecisionEvent, DecisionJournal, and trade-episode storage keep
their real owners; this module only composes typed reads against those
owners and must stay the single ACL for these kinds.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from jsonschema import Draft7Validator


def _private_record_owner(record: Dict[str, Any]) -> str:
    """Extract owner ID from private record using canonical Agora property keys."""
    for key in (
        "createdBy", "created_by", "user_id", "userId",
        "owner_id", "ownerId", "operator_id", "operatorId", "author"
    ):
        clean = str(record.get(key) or "").strip()
        if clean:
            return clean
    owner_ref = record.get("owner_ref") if isinstance(record.get("owner_ref"), dict) else {}
    return str(owner_ref.get("user_id") or owner_ref.get("owner_id") or "").strip()


def is_agora_private_record_visible(
    record: Dict[str, Any],
    identity: Any,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> bool:
    """Evaluate tenant and ownership visibility for a single record.

    Enforces strict tenant isolation, discards non-dict objects, and checks
    user ownership for private visibility rows.
    """
    if not isinstance(record, dict):
        return False

    from services.control_plane.bff.agora.identity.scope import resolve_canonical_agora_scope

    resolved_tenant, resolved_user = resolve_canonical_agora_scope(
        identity,
        tenant_id=tenant_id,
        user_id=user_id,
        utc_now=utc_now,
    )
    identity_tenant = str(resolved_tenant or "").strip()
    record_tenant = str(record.get("tenant_id") or record.get("tenantId") or "").strip()

    # Strict tenant isolation
    if identity_tenant:
        if not record_tenant or record_tenant != identity_tenant:
            return False
    elif record_tenant:
        return False

    visibility = str(record.get("visibility") or "private").strip().lower()
    owner = _private_record_owner(record)
    if visibility != "private" or not owner:
        return True

    operator_id = str(getattr(identity, "operator_id", "") or "").strip() if identity else ""
    allowed_users = {u for u in (resolved_user, operator_id) if u}
    return owner in allowed_users


def filter_agora_private_records(
    records: List[Any],
    identity: Any,
    *,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> List[Dict[str, Any]]:
    """Filter records by tenant isolation and private visibility, dropping non-dict records."""
    from services.control_plane.bff.agora.identity.scope import resolve_canonical_agora_scope

    resolved_tenant, resolved_user = resolve_canonical_agora_scope(
        identity,
        tenant_id=tenant_id,
        user_id=user_id,
        utc_now=utc_now,
    )
    return [
        record
        for record in records
        if isinstance(record, dict)
        and is_agora_private_record_visible(
            record,
            identity,
            tenant_id=resolved_tenant,
            user_id=resolved_user,
            utc_now=utc_now,
        )
    ]


def resolve_agora_interaction_context_ref(
    *,
    kind: str,
    ref_id: str,
    ref_version: Optional[str] = None,
    resolved: Any,
    session: Dict[str, Any],
    context_refs: List[Dict[str, Any]],
    authorization: Optional[str] = None,
    source_route: Optional[str] = None,
    focused_object: Optional[Dict[str, Any]] = None,
    # Explicit injected dependencies:
    read_store: Optional[Any] = None,
    extract_identity: Optional[Callable[[Optional[str]], Any]] = None,
    require_read_role: Optional[Callable[[Any], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    trade_journal_store_name: str = "PANTHEON_BFF_TRADE_EPISODES_STORE",
    trade_journal_loader: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    trade_episode_schema_path: Optional[Path] = None,
    persona_directory_snapshot_fn: Optional[Callable[[str], Any]] = None,
    persona_record_tenant_id_fn: Optional[Callable[[Mapping[str, Any]], str]] = None,
    trade_journal_allowed_fn: Optional[Callable[[Any, str], bool]] = None,
    filter_private_records_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    trading_room_store_fn: Optional[Callable[[], Any]] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Resolve context refs with audience verification and fail-closed tenant isolation.

    Decoupled pure service function with zero direct imports from bff.main composition root.
    Preserves exact production behavior:
    1. 503 rejection for unavailable kinds and focused Decision Event objects.
    2. Decision Event lookup and audience verification.
    3. Trade Journal episode schema validation and complete 10-point audience verification.
    4. Fallback to Governance Decision Journal with fail-closed tenant/user filtering.
    """
    if focused_object is None:
        focused_object = {}

    identity = extract_identity(authorization) if callable(extract_identity) else None
    if callable(require_read_role):
        require_read_role(identity)

    # 1. Unavailable sources
    if kind in {"position", "performance_window", "human_inbox_item"}:
        if callable(bff_error):
            raise bff_error(
                503,
                "DEPENDENCY_UNAVAILABLE",
                f"Canonical {kind} interaction scope is unavailable",
                f"{kind} does not yet expose a tenant-and-user-scoped ownership receipt",
                precondition_failed=f"{kind}_scope_unavailable",
            )
        raise RuntimeError(f"Canonical {kind} interaction scope is unavailable")

    # 2. Decision Event
    if kind == "decision_event":
        # Preserve focused Decision Event 503 rejection
        if (
            isinstance(focused_object, dict)
            and focused_object.get("kind") == "decision_event"
            and str(focused_object.get("id") or "") == ref_id
        ):
            if callable(bff_error):
                raise bff_error(
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "Focused Decision Event interaction source is unavailable",
                    "No canonical frontend Decision Event source-route owner is registered yet",
                    precondition_failed="decision_event_source_route_unavailable",
                )
            raise RuntimeError("Focused Decision Event interaction source is unavailable")

        if callable(trading_room_store_fn):
            store = trading_room_store_fn()
        else:
            from services.control_plane.bff.agora.trading_room.router import (
                _get_store as _get_trading_room_store,
            )
            store = _get_trading_room_store()

        event = store.get_decision_event(ref_id)
        if not isinstance(event, dict):
            return {"row": None, "audience_verified": False}
        event_strategy = str(event.get("strategy_id") or "")
        event_version = str(event.get("strategy_spec_registry_id") or "")
        scoped_strategy = str(session.get("strategy_id") or "")
        scoped_version = str(session.get("active_strategy_spec_registry_id") or "")
        audience_verified = bool(
            event_strategy
            and event_strategy == scoped_strategy
            and (not event_version or event_version == scoped_version)
        )
        return {"row": event, "audience_verified": audience_verified}

    # 3. Journal Entry (Trade Journal Episode or Governance Decision Journal)
    if kind == "journal_entry":
        # Resolve trade journal loader
        if callable(trade_journal_loader):
            episodes = trade_journal_loader(trade_journal_store_name)
        else:
            from services.control_plane.bff.trade_journal import _load as _load_trade_journal
            episodes = _load_trade_journal(trade_journal_store_name)

        matches = [
            row for row in (episodes or [])
            if str(row.get("trade_episode_id") or "") == ref_id
        ]
        if len(matches) == 1:
            episode = matches[0]

            # Resolve projection schema path: parents[4] from context_resolver.py
            # points to repo root / services, reaching services/telemetry/trade_episode_projection.schema.json
            if trade_episode_schema_path is not None:
                schema_path = trade_episode_schema_path
            else:
                schema_path = (
                    Path(__file__).resolve().parents[4]
                    / "telemetry"
                    / "trade_episode_projection.schema.json"
                )

            try:
                projection_schema = json.loads(schema_path.read_text(encoding="utf-8"))
                projection_valid = Draft7Validator(projection_schema).is_valid(episode)
            except (OSError, TypeError, ValueError):
                projection_valid = False

            # Complete episode audience and scope verification checks
            persona_id = str(episode.get("persona_id") or "")
            referenced_personas = {
                str(item.get("id") or "")
                for item in context_refs
                if item.get("kind") == "persona"
            }

            # Persona existence and directory snapshot
            persona = None
            if callable(persona_directory_snapshot_fn):
                snapshot = persona_directory_snapshot_fn(str(resolved.tenant_id or "").strip())
                persona = getattr(snapshot, "records_by_id", {}).get(persona_id)

            # Persona tenant extraction
            if callable(persona_record_tenant_id_fn):
                record_tenant_fn = persona_record_tenant_id_fn
            else:
                from services.control_plane.bff.personas.service import _persona_record_tenant_id
                record_tenant_fn = _persona_record_tenant_id

            # Trade journal authorization ACL
            if callable(trade_journal_allowed_fn):
                journal_allowed_fn = trade_journal_allowed_fn
            else:
                from services.control_plane.bff.trade_journal import _allowed as _trade_journal_allowed
                journal_allowed_fn = _trade_journal_allowed

            episode_strategy = str(episode.get("strategy_id") or "")
            artifact_id = str(episode.get("artifact_id") or "")
            artifact_version = str(episode.get("artifact_version") or "")
            episode_strategy_version = str(episode.get("strategy_spec_registry_id") or "")
            scoped_strategy = str(session.get("strategy_id") or "")
            scoped_version = str(session.get("active_strategy_spec_registry_id") or "")

            source = urlsplit(str(source_route or ""))
            source_path = unquote(source.path).rstrip("/")
            source_query = parse_qs(source.query, keep_blank_values=True)
            focused_is_episode = (
                isinstance(focused_object, dict)
                and focused_object.get("kind") == "journal_entry"
                and str(focused_object.get("id") or "") == ref_id
            )
            canonical_persona_journal_route = bool(
                source_path == f"/management/personas/{persona_id}"
                and source_query.get("tab") == ["tradeJournal"]
                and not source.fragment
            )
            canonical_workshop_route = bool(
                not focused_is_episode
                and source_path == f"/agora/strategy-workshop/{session.get('workshop_id')}"
                and not source.fragment
            )

            # All 10 audience conditions preserved from main.py
            audience_verified = bool(
                projection_valid
                and persona_id
                and episode_strategy
                and artifact_id
                and artifact_version
                and persona_id in referenced_personas
                and isinstance(persona, dict)
                and record_tenant_fn(persona) == resolved.tenant_id
                and journal_allowed_fn(identity, persona_id)
                and episode_strategy == scoped_strategy
                and (not episode_strategy_version or episode_strategy_version == scoped_version)
                and (canonical_persona_journal_route or canonical_workshop_route)
            )
            return {"row": episode, "audience_verified": audience_verified}

        # Fallback to Decision Journal in Governance domain
        from services.control_plane.bff.agora.identity.scope import resolve_canonical_agora_scope

        scoped_tenant, scoped_user = resolve_canonical_agora_scope(
            identity,
            tenant_id=getattr(resolved, "tenant_id", None),
            user_id=getattr(resolved, "user_id", None),
            utc_now=utc_now,
        )
        if read_store is None:
            return {"row": None, "audience_verified": False}

        try:
            journal_entries = read_store.list_decision_journal_entries(
                tenant_id=scoped_tenant, user_id=scoped_user
            )
        except TypeError:
            journal_entries = read_store.list_decision_journal_entries()

        # Execute bound or canonical private record filter, retaining non-dict filtering
        if callable(filter_private_records_fn):
            journal_rows = filter_private_records_fn(
                journal_entries,
                identity,
                tenant_id=scoped_tenant,
                user_id=scoped_user,
            )
        else:
            journal_rows = filter_agora_private_records(
                journal_entries,
                identity,
                tenant_id=scoped_tenant,
                user_id=scoped_user,
                utc_now=utc_now,
            )

        journal = next(
            (
                row for row in journal_rows
                if isinstance(row, dict) and str(row.get("id") or row.get("entry_id") or "") == ref_id
            ),
            None,
        )
        # Legacy Decision Journal rows are returned for exact not-found/error
        # semantics, but without explicit scope they are intentionally not
        # elevated to an audience-verified receipt.
        return {"row": journal, "audience_verified": False}

    if callable(bff_error):
        raise bff_error(
            503,
            "DEPENDENCY_UNAVAILABLE",
            f"Canonical {kind} readback is unavailable",
            f"{kind}_store_unavailable",
            precondition_failed=f"{kind}_store_unavailable",
        )
    raise RuntimeError(f"Canonical {kind} readback is unavailable")
