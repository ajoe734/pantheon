"""Durable Postgres-backed RegistryEntry owner store.

Selected via ``REGISTRY_STORE_BACKEND=postgres`` (see :func:`build_registry_store`
in ``services/registry/storage.py``). This is the single production write
authority for StrategySpec content, immutable versions, RegistryEntry
identities and artifact-state per architecture-resumption-sa-sd.md §3.1: no
independent full-spec/revision copy, no process-memory production fallback.

Every RegistryEntry lives as one JSONB row in ``registry.entries`` (via
``PostgresJsonOwnerStore``). Idempotent command receipts for the metadata CAS
update path live in a second table, ``registry.command_receipts``, committed
in the *same* Postgres transaction as the entry mutation it records (see
:meth:`PostgresRegistryStore.commit_metadata_cas`), so a crash between
"entry written" and "receipt written" cannot happen and a same command key
replayed later returns the original committed version instead of re-running
the mutation or fabricating a second one.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Callable, Optional, Sequence

from services.foundation.postgres_json_store import PostgresJsonOwnerStore, quote_pg_identifier

from .models import (
    DeploymentStage,
    DeploymentSummary,
    RegistryEntry,
    RegistryEntryCreate,
    entry_payloads_cas_equal,
    utc_now_iso,
)
from .storage import (
    DISCOVERY_ENTRY_KEYS,
    DISCOVERY_EXCLUDED_STATES,
    LINEAGE_LIST_FIELDS,
    LINEAGE_META_FIELDS,
    LINEAGE_ROW_KEYS,
    LINEAGE_SCALAR_FIELDS,
    PERSONA_BINDING_INNER_META_KEYS,
    PERSONA_BINDING_META_KEYS,
    PERSONA_BINDING_ROW_KEYS,
    PERSONA_BINDING_SPEC_KEYS,
    REGIME_EVAL_MODEL_META_KEYS,
    REGIME_EVAL_MODEL_ROW_KEYS,
    REGIME_EVAL_STRATEGY_INNER_META_KEYS,
    REGIME_EVAL_STRATEGY_META_KEYS,
    REGIME_EVAL_STRATEGY_ROW_KEYS,
    REGIME_EVAL_STRATEGY_SPEC_KEYS,
    STRATEGY_ARTIFACT_EVAL_KEYS,
    STRATEGY_ARTIFACT_ROW_KEYS,
    STRATEGY_SUMMARY_EXEC_KEYS,
    STRATEGY_SUMMARY_GOV_KEYS,
    STRATEGY_SUMMARY_INDEX_INNER_META_KEYS,
    STRATEGY_SUMMARY_INDEX_META_KEYS,
    STRATEGY_SUMMARY_INDEX_PROV_KEYS,
    STRATEGY_SUMMARY_INDEX_ROW_KEYS,
    STRATEGY_SUMMARY_INDEX_SPEC_KEYS,
    STRATEGY_SUMMARY_INDEX_SCOPE_KEYS,
    STRATEGY_SUMMARY_INDEX_TITLE_KEYS,
    STRATEGY_SUMMARY_INNER_META_KEYS,
    STRATEGY_SUMMARY_MARKET_KEYS,
    STRATEGY_SUMMARY_META_KEYS,
    STRATEGY_SUMMARY_PROV_KEYS,
    STRATEGY_SUMMARY_ROW_KEYS,
    STRATEGY_SUMMARY_SPEC_KEYS,
    RegistryConcurrentUpdateError,
    _CompiledIndexRow,
    _CompiledIndexRowList,
    _compile_strategy_summary_index_row,
    build_strategy_summary_index_payload,
    discovery_candidate_from_payload,
    encode_strategy_artifact_page_token,
    page_strategy_artifact_rows,
    project_cached_summary_index_row,
    strategy_artifact_sort_key,
)


class DivergentCommandReplayError(ValueError):
    """A command key was reused with a different requested mutation than the
    one it originally committed. Idempotent replay only preserves the
    original committed version for the exact same request; a divergent
    request under the same key is a caller bug, not a silently accepted
    second version."""

    def __init__(self, command_key: str) -> None:
        super().__init__(
            f"command_key {command_key!r} was already committed with a different request payload; "
            "idempotent replay requires an identical request."
        )
        self.command_key = command_key


def _request_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _new_entry(
    payload: RegistryEntryCreate,
    registry_id: str,
    *,
    actor: Optional[dict[str, Any]] = None,
) -> RegistryEntry:
    now = utc_now_iso()
    return RegistryEntry(
        registry_id=registry_id,
        artifact_type=payload.artifact_type,
        strategy_id=payload.strategy_id,
        version=payload.version,
        artifact_state=payload.artifact_state,
        lineage=payload.lineage,
        storage_ref=payload.storage_ref,
        checksum=payload.checksum,
        producer_run_id=payload.producer_run_id,
        evaluation_summary=payload.evaluation_summary,
        rollback_target=payload.rollback_target,
        metadata=payload.metadata,
        replication_success=payload.replication_success,
        replication_proof=payload.replication_proof,
        created_at=now,
        updated_at=now,
        last_actor=actor,
        owner_tenant=str((actor or {}).get("tenant") or "").strip() or None,
    )


# Ordered execution-bundle catalog page (ARTIFACT-CATALOG-INDEXED-PAGING-20261115). The predicate is
# literal so the partial index below is provably applicable; ``"C"`` collation matches Python code-point order.
_ARTIFACT_CATALOG_PRED_SQL = (
    "jsonb_typeof(payload) = 'object' AND payload ->> 'artifact_type' = 'execution_bundle'"
)
_ARTIFACT_CATALOG_CREATED_SQL = "(COALESCE(payload ->> 'created_at', '') COLLATE \"C\")"
_ARTIFACT_CATALOG_ID_SQL = "(COALESCE(payload ->> 'registry_id', '') COLLATE \"C\")"


_DISCOVERY_SELECT_SQL = (
    "jsonb_build_object("
    + ", ".join(f"'{k}', payload -> '{k}'" for k in DISCOVERY_ENTRY_KEYS)
    + ")::text"
)


# ALPHA-TEMPLATE-READ-FAST-20261126: type-filtered reads go through an expression
# index on the artifact type, so rows of other artifact types (hosted: ~10k wide
# strategy_spec payloads) are neither read nor detoasted. Forward-only and additive.
ARTIFACT_TYPE_INDEX_SUFFIX = "_artifact_type_idx"
ARTIFACT_TYPE_INDEX_SQL = "(payload ->> 'artifact_type')"

# REGISTRY-SUMMARY-PAGE-NO-RESCAN-20261127: Phase 1 of strategy_summary_page reads
# pre-projected candidate-index fields from stored columns (summary_index JSONB,
# summary_rev BIGINT) kept current on every write path via a BEFORE INSERT OR UPDATE
# OF payload trigger (and idempotently backfilled in bounded batches at bootstrap),
# paired with a stamp-validated in-process Phase 1 cache checked by one cheap
# index-only query on entries_strategy_summary_idx.
SUMMARY_INDEX_COLUMN = "summary_index"
SUMMARY_REV_COLUMN = "summary_rev"
SUMMARY_INDEX_SUFFIX = "_strategy_summary_idx"
SUMMARY_INDEX_TRIGGER_SUFFIX = "_strategy_summary_index_tg"
SUMMARY_INDEX_BACKFILL_BATCH_SIZE = 1000
SUMMARY_INDEX_BACKFILL_MAX_BATCHES = 50
SUMMARY_INDEX_MAX_TENANT_VIEWS = 16


def strategy_spec_summary_index_stamp_sql(table: str) -> str:
    """One cheap index-only query returning ``(count, max_rev, sum_rev)`` over stored summary-index rows."""
    return (
        f"SELECT count(*), coalesce(max({SUMMARY_REV_COLUMN}), 0)::bigint, "
        f"coalesce(sum({SUMMARY_REV_COLUMN}), 0)::bigint "
        f"FROM {table} WHERE {SUMMARY_INDEX_COLUMN} IS NOT NULL"
    )


def strategy_spec_summary_index_stored_select_sql(table: str) -> str:
    """Cold Phase 1 read of stored ``summary_index`` column (never evaluates ``payload`` expressions)."""
    return (
        f"SELECT record_id, coalesce({SUMMARY_REV_COLUMN}, 0)::bigint, ({SUMMARY_INDEX_COLUMN})::text "
        f"FROM {table} WHERE {SUMMARY_INDEX_COLUMN} IS NOT NULL "
        "ORDER BY updated_at ASC, record_id ASC"
    )


def strategy_spec_summary_index_delta_sql(table: str) -> str:
    """Indexed delta Phase 1 read of stored ``summary_index`` rows written since a revision watermark."""
    return (
        f"SELECT record_id, coalesce({SUMMARY_REV_COLUMN}, 0)::bigint, ({SUMMARY_INDEX_COLUMN})::text "
        f"FROM {table} WHERE {SUMMARY_INDEX_COLUMN} IS NOT NULL AND {SUMMARY_REV_COLUMN} > %s "
        "ORDER BY updated_at ASC, record_id ASC"
    )


def strategy_spec_summary_index_stored_tenant_select_sql(
    table: str, visible_tenants: Optional[frozenset] = None,
) -> tuple[str, tuple[Any, ...]]:
    """Direct uncached stored-column Phase 1 query filtered by ``visible_tenants`` without touching ``payload``."""
    where = [f"{SUMMARY_INDEX_COLUMN} IS NOT NULL"]
    params: list[Any] = []
    if visible_tenants is not None:
        named = sorted(t for t in visible_tenants if t is not None)
        clauses = [f"{SUMMARY_INDEX_COLUMN} ->> 'owner_tenant' = ANY(%s::text[])"]
        params.append(named)
        if None in visible_tenants:
            clauses.append(f"{SUMMARY_INDEX_COLUMN} ->> 'owner_tenant' IS NULL")
        where.append("(" + " OR ".join(clauses) + ")")
    sql = (
        f"SELECT record_id, coalesce({SUMMARY_REV_COLUMN}, 0)::bigint, ({SUMMARY_INDEX_COLUMN})::text "
        f"FROM {table} WHERE {' AND '.join(where)} "
        "ORDER BY updated_at ASC, record_id ASC"
    )
    return sql, tuple(params)


def _decode_stored_summary_index_row(raw_json: str) -> _CompiledIndexRow:
    data = json.loads(raw_json)
    owner_tenant = data.pop("owner_tenant", None)
    row = _CompiledIndexRow(data, owner_tenant=owner_tenant)
    row._compiled = _compile_strategy_summary_index_row(row)
    return row


def _strategy_summary_index_build_function_sql(fn_build: str) -> str:
    """PL/pgSQL IMMUTABLE function extracting the candidate-index JSONB from a RegistryEntry payload."""
    return f"""
CREATE OR REPLACE FUNCTION {fn_build}(raw_payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
AS $fn$
DECLARE
    p jsonb;
    m jsonb;
    mp jsonb;
    s jsonb;
    sp jsonb;
    sm jsonb;
    meta_out jsonb;
    spec_out jsonb;
    inner_meta_out jsonb;
    row_out jsonb;
BEGIN
    IF raw_payload IS NULL THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(raw_payload) = 'string' THEN
        BEGIN
            p := (raw_payload #>> '{{}}')::jsonb;
        EXCEPTION WHEN OTHERS THEN
            RETURN NULL;
        END;
    ELSE
        p := raw_payload;
    END IF;
    IF jsonb_typeof(p) <> 'object' OR COALESCE(p ->> 'artifact_type', '') <> 'strategy_spec' THEN
        RETURN NULL;
    END IF;

    m := CASE WHEN jsonb_typeof(p -> 'metadata') = 'object' THEN p -> 'metadata' ELSE NULL END;
    mp := CASE WHEN m IS NOT NULL AND jsonb_typeof(m -> 'provenance') = 'object' THEN m -> 'provenance' ELSE NULL END;
    s := CASE WHEN m IS NOT NULL AND jsonb_typeof(m -> 'strategy_spec') = 'object' THEN m -> 'strategy_spec' ELSE NULL END;
    sp := CASE WHEN s IS NOT NULL AND jsonb_typeof(s -> 'provenance') = 'object' THEN s -> 'provenance' ELSE NULL END;
    sm := CASE WHEN s IS NOT NULL AND jsonb_typeof(s -> 'metadata') = 'object' THEN s -> 'metadata' ELSE NULL END;

    IF sm IS NOT NULL THEN
        inner_meta_out := jsonb_strip_nulls(jsonb_build_object(
            'persona_id', sm -> 'persona_id',
            'lifecycle_state', sm -> 'lifecycle_state',
            'state', sm -> 'state',
            'status', sm -> 'status',
            'data_scope', sm -> 'data_scope',
            'dataScope', sm -> 'dataScope'
        ));
    ELSE
        inner_meta_out := NULL;
    END IF;

    IF s IS NOT NULL THEN
        spec_out := jsonb_strip_nulls(jsonb_build_object(
            'strategy_id', s -> 'strategy_id',
            'spec_version', s -> 'spec_version',
            'lifecycle_state', s -> 'lifecycle_state',
            'state', s -> 'state',
            'status', s -> 'status',
            'persona_ids', s -> 'persona_ids',
            'created_at', s -> 'created_at',
            'updated_at', s -> 'updated_at',
            'last_modified_at', s -> 'last_modified_at',
            'title', s -> 'title',
            'name', s -> 'name',
            'provenance', CASE WHEN sp IS NOT NULL THEN jsonb_strip_nulls(jsonb_build_object('created_at', sp -> 'created_at')) ELSE NULL END,
            'metadata', inner_meta_out
        ));
    ELSE
        spec_out := NULL;
    END IF;

    IF m IS NOT NULL THEN
        meta_out := jsonb_strip_nulls(jsonb_build_object(
            'spec_version_id', m -> 'spec_version_id',
            'spec_version', m -> 'spec_version',
            'version', m -> 'version',
            'personaIds', m -> 'personaIds',
            'persona_ids', m -> 'persona_ids',
            'persona_id', m -> 'persona_id',
            'lifecycle_state', m -> 'lifecycle_state',
            'state', m -> 'state',
            'status', m -> 'status',
            'title', m -> 'title',
            'name', m -> 'name',
            'data_scope', m -> 'data_scope',
            'dataScope', m -> 'dataScope',
            'provenance', CASE WHEN mp IS NOT NULL THEN jsonb_strip_nulls(jsonb_build_object('created_at', mp -> 'created_at')) ELSE NULL END,
            'strategy_spec', spec_out
        ));
    ELSE
        meta_out := '{{}}'::jsonb;
    END IF;

    row_out := jsonb_strip_nulls(jsonb_build_object(
        'registry_id', p -> 'registry_id',
        'artifact_type', p -> 'artifact_type',
        'artifact_state', p -> 'artifact_state',
        'strategy_id', p -> 'strategy_id',
        'version', p -> 'version',
        'owner_tenant', p -> 'owner_tenant',
        'created_at', p -> 'created_at',
        'updated_at', p -> 'updated_at'
    ));
    IF (p -> 'created_at') IS NOT NULL AND jsonb_typeof(p -> 'created_at') = 'null' THEN
        row_out := row_out || '{{"created_at": null}}'::jsonb;
    END IF;
    IF (p -> 'updated_at') IS NOT NULL AND jsonb_typeof(p -> 'updated_at') = 'null' THEN
        row_out := row_out || '{{"updated_at": null}}'::jsonb;
    END IF;
    RETURN row_out || jsonb_build_object('metadata', meta_out);
END;
$fn$;
""".strip()


def _strategy_summary_index_trigger_function_sql(
    fn_tg: str, fn_build: str, seq_ident: str,
) -> str:
    return f"""
CREATE OR REPLACE FUNCTION {fn_tg}()
RETURNS trigger
LANGUAGE plpgsql
AS $tg$
BEGIN
    NEW.{SUMMARY_INDEX_COLUMN} := {fn_build}(NEW.payload);
    IF NEW.{SUMMARY_INDEX_COLUMN} IS NOT NULL THEN
        NEW.{SUMMARY_REV_COLUMN} := nextval('{seq_ident}');
    ELSE
        NEW.{SUMMARY_REV_COLUMN} := NULL;
    END IF;
    RETURN NEW;
END;
$tg$;
""".strip()


def artifact_type_select_sql(table: str) -> str:
    """Rows of one artifact type, ``updated_at`` ascending, as JSON text.

    Object payloads match ``ARTIFACT_TYPE_INDEX_SQL`` exactly (index scan);
    legacy JSON-string payloads are reached through the partial
    ``_legacy_str_payload_idx`` and unwrapped. Two ``%s`` params: the type, twice.
    """
    return (
        "SELECT p FROM ("
        f"SELECT updated_at, (payload)::text AS p FROM {table} "
        f"WHERE {ARTIFACT_TYPE_INDEX_SQL} = %s "
        "UNION ALL "
        f"SELECT updated_at, (payload #>> '{{}}') AS p FROM {table} "
        "WHERE jsonb_typeof(payload) = 'string' "
        "AND ((payload #>> '{}')::jsonb ->> 'artifact_type') = %s"
        ") AS rows ORDER BY updated_at ASC"
    )


class PostgresRegistryStore:
    """Durable RegistryEntry owner store with the same call surface as the
    in-memory ``RegistryStore`` test double, so ``RegistryService`` (split_api.py)
    needs no changes to select this backend."""

    def __init__(
        self,
        *,
        dsn: str,
        entries_table: str = "registry.entries",
        receipts_table: str = "registry.command_receipts",
        bootstrap: bool = True,
    ) -> None:
        self._entries = PostgresJsonOwnerStore(
            dsn=dsn,
            table=entries_table,
            owner_service="registry-svc",
            bootstrap=bootstrap,
            family_keep_index_sql=(PERSONA_BINDING_FAMILY_SQL, PERSONA_BINDING_BOUND_SQL),
        )
        self._receipts = PostgresJsonOwnerStore(
            dsn=dsn, table=receipts_table, owner_service="registry-svc", bootstrap=bootstrap,
        )
        self._init_summary_index_cache_state()
        if bootstrap:
            self._ensure_artifact_catalog_indexes()
            self._ensure_artifact_type_index()
            self._ensure_strategy_summary_index()
        self.full_summary_rows_extracted: int = 0
        self.full_summary_row_ids_extracted: list[str] = []

    def _init_summary_index_cache_state(self) -> None:
        if not hasattr(self, "_summary_index_lock"):
            self._summary_index_lock = threading.Lock()
        if not hasattr(self, "_summary_index_by_record_id"):
            self._summary_index_by_record_id: Optional[dict[str, tuple[int, _CompiledIndexRow]]] = None
        if not hasattr(self, "_summary_index_stamp"):
            self._summary_index_stamp: Optional[tuple[int, int, int]] = None
        if not hasattr(self, "_summary_index_tenant_views"):
            self._summary_index_tenant_views: dict[tuple[Any, bool, bool], _CompiledIndexRowList] = {}
        if not hasattr(self, "_summary_index_ready"):
            self._summary_index_ready: bool = False
        if not hasattr(self, "_summary_index_cache_enabled"):
            self._summary_index_cache_enabled: bool = True
        if not hasattr(self, "last_summary_index_backfill"):
            self.last_summary_index_backfill: Optional[dict[str, Any]] = None
        if not hasattr(self, "phase1_index_sql_reads"):
            self.phase1_index_sql_reads: int = 0
        if not hasattr(self, "phase1_index_delta_reads"):
            self.phase1_index_delta_reads: int = 0
        if not hasattr(self, "phase1_index_cache_hits"):
            self.phase1_index_cache_hits: int = 0
        if not hasattr(self, "phase1_index_rows_fetched"):
            self.phase1_index_rows_fetched: int = 0
        if not hasattr(self, "phase1_payload_rescans"):
            self.phase1_payload_rescans: int = 0

    def invalidate_strategy_summary_index_cache(self, *, full: bool = False) -> None:
        """Invalidate the in-process Phase 1 summary-index cache (or tenant views)."""
        self._init_summary_index_cache_state()
        with self._summary_index_lock:
            self._summary_index_tenant_views.clear()
            if full:
                self._summary_index_by_record_id = None
                self._summary_index_stamp = None

    # -- Write operations ---------------------------------------------------

    def create(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict[str, Any]] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> RegistryEntry:
        """Create a new entry, atomically reserving ``unique_fields`` if given.

        Reviewer finding 3: this previously called ``create_if_absent`` with
        no ``unique_fields``, so it reserved only the (effectively random,
        caller-generated) ``registry_id`` and left the
        (strategy_id, version, artifact_type) composite identity completely
        unguarded on this path — two concurrent ``register()`` calls under
        two different registry_ids could both commit the same revision
        identity. A genuine collision here (a *different* registry_id
        already holds the composite identity) is a caller error, not a
        silent "return the other one" outcome the way ``create_if_absent``'s
        idempotent-replay semantics are, so it raises instead.
        """
        entry, created = self.create_if_absent(
            payload, registry_id, actor=actor, unique_fields=unique_fields,
        )
        if not created and entry.registry_id != registry_id:
            from .storage import RegistryUniqueViolationError

            raise RegistryUniqueViolationError(registry_id, unique_fields, entry.registry_id)
        return entry

    def create_if_absent(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict[str, Any]] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create an entry, or return the durable existing entry unchanged.

        Uses ``insert_if_absent`` so two processes racing on the same
        ``registry_id`` (a same-key divergent-or-duplicate create request)
        commit exactly one row; the loser gets back the winner's durable
        entry instead of silently overwriting it. ``unique_fields`` (e.g.
        ``("strategy_id", "version", "artifact_type")``) additionally
        reserves a composite identity under a table-level lock held for the
        whole read/decide/write sequence, so two *different* registry_ids
        racing on the same (strategy_id, version, artifact_type) tuple also
        commit exactly one winner — architecture-resumption-sa-sd.md §3.2's
        "immutable revision identity" requirement.
        """
        entry = _new_entry(payload, registry_id, actor=actor)
        with self._entries.transaction() as conn:
            created, canonical = self._entries.insert_if_absent(
                registry_id, entry.to_dict(), unique_fields=unique_fields, conn=conn,
            )
            if created:
                self._store_creation_receipt(registry_id, entry.to_dict(), conn=conn)
        if created:
            self.invalidate_strategy_summary_index_cache(full=False)
            return entry, True
        # Reviewer finding 5: the *comparison* a caller runs against a
        # same-registry_id collision must use the row's immutable
        # original-creation content (see :meth:`get_creation_receipt`), never
        # whatever it has mutated into since (advance/metadata edits) — but
        # the row returned here stays the live current entry, since a
        # replayed create is not a request to revert real progress (e.g. an
        # approval) back to its original draft state. Callers that need the
        # immutable original content for their own equality check fetch it
        # separately via :meth:`get_creation_receipt`.
        return RegistryEntry.from_dict(canonical), False

    def get_creation_receipt(self, registry_id: str) -> Optional[RegistryEntry]:
        """Return the entry exactly as it was at its first successful
        creation, if a receipt was recorded (see :meth:`_store_creation_receipt`).

        ``None`` for a row created before this receipt existed (a legacy
        row) or one that was never created through ``create_if_absent``/
        ``register_strategy_spec_revision`` (e.g. ``put``-only bootstrap).
        Callers comparing a same-identity replay's claimed content should
        prefer this over the live row's current (possibly since-mutated)
        fields, but must still return the live row to any HTTP caller.
        """
        original = self._read_creation_receipt(registry_id)
        return RegistryEntry.from_dict(original) if original is not None else None

    def get_command_receipt(
        self,
        command_key: str,
        registry_id: str,
        *,
        actor: Optional[dict[str, Any]] = None,
        command_type: str = "metadata",
    ) -> Optional[dict[str, Any]]:
        """Return the durable command receipt committed for a command_key."""
        scoped_key = self.receipt_key(command_key, registry_id, actor=actor, command_type=command_type)
        receipt = self._receipts.get(scoped_key)
        if receipt is None and command_type == "create":
            scoped_key_reg = self.receipt_key(command_key, "register_entry", actor=actor, command_type="create")
            receipt = self._receipts.get(scoped_key_reg)
            if receipt is not None:
                committed = receipt.get("committed_entry")
                if committed is not None and committed.get("registry_id") != registry_id:
                    return None
        return receipt

    def put(self, entry: RegistryEntry) -> None:
        """Unconditional overwrite — restricted to idempotent built-in bootstrap
        registration at startup, never a caller-facing mutation path."""
        self._entries.put(entry.registry_id, entry.to_dict())
        self.invalidate_strategy_summary_index_cache(full=False)

    def _cas_entry(
        self,
        registry_id: str,
        expected: Optional[dict[str, Any]],
        new_payload: dict[str, Any],
        *,
        conn: Optional[Any] = None,
    ) -> tuple[bool, Optional[dict[str, Any]]]:
        """CAS one entry row under the shared registry equality rule.

        The JSONB ``payload = expected`` predicate compares raw stored JSON,
        so a legacy row that lacks keys the model now defaults (e.g.
        ``replication_success``/``replication_proof``) never equals the
        caller's ``to_dict()`` snapshot (REGISTRY-CAS-LEGACY-ROW-NORMALIZE-
        20261126). On a miss, ``compare_and_set`` hands back the row it
        actually saw; if that row equals ``expected`` under
        :func:`entry_payloads_cas_equal` (absent key == model default, every
        present value identical) the CAS is retried once against that exact
        raw row. The retry is itself an atomic JSONB-equality CAS, so a
        writer that changed the row in between still makes it fail, and any
        real field difference never reaches the retry.
        """
        ok, canonical = self._entries.compare_and_set(
            registry_id, expected, new_payload, conn=conn,
        )
        if ok or expected is None or canonical is None or canonical == expected:
            if ok:
                self.invalidate_strategy_summary_index_cache(full=False)
            return ok, canonical
        if not entry_payloads_cas_equal(canonical, expected):
            return ok, canonical
        ok, canonical = self._entries.compare_and_set(
            registry_id, canonical, new_payload, conn=conn,
        )
        if ok:
            self.invalidate_strategy_summary_index_cache(full=False)
        return ok, canonical

    def update(
        self,
        entry: RegistryEntry,
        *,
        expected: Optional[dict[str, Any]] = None,
        actor: Optional[dict[str, Any]] = None,
    ) -> RegistryEntry:
        """Commit a mutation, optionally CAS-guarded against the caller's base snapshot.

        ``expected`` must be the exact ``to_dict()`` snapshot the caller read
        via :meth:`get` before mutating ``entry`` in place — this binds the
        write to the version the caller actually observed, not whatever is
        latest in the database at write time. Omitting ``expected`` performs
        an unconditional overwrite (used only by legacy internal call sites
        that predate CAS enforcement); new mutation paths must always pass it.
        """
        entry.updated_at = utc_now_iso()
        if actor is not None:
            entry.last_actor = actor
        new_payload = entry.to_dict()
        ok, canonical = self._cas_entry(entry.registry_id, expected, new_payload)
        if not ok:
            if expected is None:
                # No guard was requested but the row does not exist at all.
                raise RegistryConcurrentUpdateError(entry.registry_id)
            raise RegistryConcurrentUpdateError(entry.registry_id)
        return RegistryEntry.from_dict(canonical)

    @staticmethod
    def receipt_key(
        command_key: str,
        registry_id: str,
        *,
        actor: Optional[dict[str, Any]] = None,
        command_type: str = "generic",
    ) -> str:
        """Scope an idempotent command receipt by tenant/actor/command-type/aggregate.

        A bare ``command_key`` is not a safe idempotency scope on its own: two
        different tenants or actors could submit the same client-chosen key
        against two different registry_ids and collide on one receipt row.
        Scoping by tenant + actor + registry_id (the aggregate) + command_key
        makes the receipt identity unambiguous.

        Prior defect (reviewer finding 6): a plain ``f"{tenant}:{actor_id}:..."``
        colon-join let ``tenant="a:b", actor="c"`` collide with
        ``tenant="a", actor="b:c"`` — the delimiter can appear inside either
        field. Each component is now length-prefixed before joining (an
        unambiguous framing regardless of what characters the field
        contains) and the whole framed string is hashed to keep the key a
        fixed, storage-friendly size.

        Prior defect (reviewer finding 6, gen-8 review): without
        ``command_type`` a caller reusing the same client-chosen
        ``command_key`` for both a metadata-CAS call and an artifact-state
        advance on the same registry_id/tenant/actor would land on the exact
        same receipt row — the two call sites only avoided actually
        colliding because their divergent request digests happened to
        differ, not because the namespace was actually distinct.
        ``command_type`` (e.g. ``"metadata"``, ``"advance"``, ``"create"``)
        is now part of the framed identity so the two command kinds can
        never share a receipt row in the first place, regardless of digest.
        """
        tenant = str((actor or {}).get("tenant") or "unscoped").strip() or "unscoped"
        actor_id = str((actor or {}).get("actor_id") or "unscoped").strip() or "unscoped"
        parts = [tenant, actor_id, registry_id, command_type, command_key]
        framed = "|".join(f"{len(part)}:{part}" for part in parts)
        return hashlib.sha256(framed.encode("utf-8")).hexdigest()

    _CREATION_RECEIPT_PREFIX = "creation-receipt:"

    @classmethod
    def _creation_receipt_key(cls, registry_id: str) -> str:
        """Key for the immutable original-creation receipt of ``registry_id``.

        Reviewer finding 5: a same-identity create-if-absent replay (the
        StrategySpec/StrategyArtifact facades' "already registered, return
        the existing entry" path) previously returned whatever the row has
        become since — including later ``advance``/metadata mutations — so
        an identical create request replayed after a permitted metadata edit
        was wrongly rejected as "different content" even though it exactly
        matches what was originally submitted. This receipt freezes the
        entry snapshot exactly as it was at the moment of its first
        successful creation, independent of ``record_id``'s own key space
        (a distinct, prefixed pseudo-identity in the same receipts table).
        """
        return f"{cls._CREATION_RECEIPT_PREFIX}{registry_id}"

    def _store_creation_receipt(
        self, registry_id: str, entry_snapshot: dict[str, Any], *, conn: Any,
    ) -> None:
        """Record the immutable original-creation snapshot, in the same
        transaction as the entry's own insert (``conn`` is the shared
        connection from :meth:`PostgresJsonOwnerStore.transaction`)."""
        self._receipts.insert_if_absent(
            self._creation_receipt_key(registry_id),
            {"registry_id": registry_id, "created_entry": entry_snapshot},
            conn=conn,
        )

    def _read_creation_receipt(self, registry_id: str) -> Optional[dict[str, Any]]:
        """Read back the original-creation snapshot, if one was recorded.

        Safe to use its own fresh connection (not ``conn=``): this is only
        ever consulted after a create-if-absent collision, meaning the
        original creation (and its receipt, committed atomically alongside
        it) already committed in a prior, separate transaction.
        """
        receipt = self._receipts.get(self._creation_receipt_key(registry_id))
        if receipt is None:
            return None
        return receipt.get("created_entry")

    def commit_metadata_cas(
        self,
        *,
        registry_id: str,
        base_snapshot: dict[str, Any],
        new_metadata: Optional[dict[str, Any]],
        command_key: Optional[str] = None,
        actor: Optional[dict[str, Any]] = None,
        validate: Optional[Callable[[RegistryEntry], None]] = None,
    ) -> tuple[RegistryEntry, bool]:
        """Atomically CAS the metadata field and record an idempotent receipt.

        Both writes commit in one Postgres transaction, using a single shared
        connection (:meth:`PostgresJsonOwnerStore.transaction`) so the entry
        mutation and its receipt land together or not at all.

        The receipt row is reserved *before* the entry mutation via
        ``insert_if_absent`` (which holds a table-level lock for the rest of
        this transaction), and its ``created`` result is what decides the
        control flow — not an assumption: a fresh reservation (``created``)
        proceeds to CAS the entry and then fills in the receipt with the
        entry snapshot this transaction actually commits; an existing
        reservation (``not created``) is a replay (identical ``new_metadata``
        digest) or a divergent reuse (different digest) of the same
        tenant-scoped ``command_key``.

        A replay always returns the entry snapshot captured in that original
        receipt row (``idempotent_replay=True``) — never a fresh read of
        whatever the entry has become since, which could reflect a later,
        unrelated mutation under a *different* command_key. A replay with a
        different ``new_metadata`` under the same key raises
        :class:`DivergentCommandReplayError` instead of silently accepting a
        second version under one key.
        """
        entry = RegistryEntry.from_dict(base_snapshot)
        entry.metadata = new_metadata
        entry.updated_at = utc_now_iso()
        if actor is not None:
            entry.last_actor = actor
        new_payload = entry.to_dict()
        # The digest must cover the caller's precondition (expected_metadata,
        # carried in base_snapshot["metadata"]) as well as the target
        # metadata — reviewer finding 6: a same-key request with a *changed*
        # precondition but identical target metadata must not silently
        # report replay=true, since the caller's compare-and-swap intent
        # differs even though the end state looks the same.
        request_digest = _request_digest({
            "registry_id": registry_id,
            "expected_metadata": base_snapshot.get("metadata"),
            "metadata": new_metadata,
        })
        scoped_key = (
            self.receipt_key(command_key, registry_id, actor=actor, command_type="metadata")
            if command_key
            else None
        )

        with self._entries.transaction() as conn:
            reservation: Optional[dict[str, Any]] = None
            if scoped_key:
                # Reviewer finding 2 (gen-10 review): always lock entries
                # before ever touching receipts in this transaction, matching
                # create_if_absent/register_strategy_spec_revision's order.
                # Taking the receipts lock first (as before) let this method
                # deadlock in Postgres against a concurrent create path that
                # legitimately locks entries first — see
                # PostgresJsonOwnerStore.lock_table's docstring.
                self._entries.lock_table(conn=conn)
                reservation = {
                    "command_key": command_key,
                    "receipt_key": scoped_key,
                    "registry_id": registry_id,
                    "request_digest": request_digest,
                    "committed_entry": None,
                    "committed_at": None,
                }
                reserved, receipt_payload = self._receipts.insert_if_absent(
                    scoped_key, reservation, conn=conn,
                )
                if not reserved:
                    if receipt_payload.get("request_digest") != request_digest:
                        raise DivergentCommandReplayError(command_key)
                    committed_entry = receipt_payload.get("committed_entry")
                    if committed_entry is None:
                        # Another in-flight transaction reserved this key but
                        # has not yet committed the mutation it records —
                        # fail closed rather than return an incomplete replay.
                        raise RegistryConcurrentUpdateError(registry_id)
                    return RegistryEntry.from_dict(committed_entry), True

            if validate is not None:
                validate(RegistryEntry.from_dict(base_snapshot))

            ok, canonical = self._cas_entry(
                registry_id, base_snapshot, new_payload, conn=conn,
            )
            if not ok:
                raise RegistryConcurrentUpdateError(registry_id)

            if scoped_key:
                finalized = dict(reservation)
                finalized["committed_entry"] = canonical
                finalized["committed_at"] = utc_now_iso()
                filled, _ = self._receipts.compare_and_set(
                    scoped_key, reservation, finalized, conn=conn,
                )
                if not filled:
                    raise RegistryConcurrentUpdateError(registry_id)
        return RegistryEntry.from_dict(canonical), False

    def commit_artifact_state_cas(
        self,
        *,
        registry_id: str,
        base_snapshot: dict[str, Any],
        target_state: Any,
        approved_at: Optional[str] = None,
        approver: Optional[str] = None,
        approval_decision_id: Optional[str] = None,
        command_key: Optional[str] = None,
        actor: Optional[dict[str, Any]] = None,
        validate: Optional[Callable[[RegistryEntry], None]] = None,
        expected_artifact_state: Optional[str] = None,
        expected_version: Optional[str] = None,
        expected_updated_at: Optional[str] = None,
    ) -> tuple[RegistryEntry, bool]:
        """Atomically CAS ``artifact_state`` (plus approval fields) and record
        an idempotent command receipt in the same transaction — mirrors
        :meth:`commit_metadata_cas`.

        ``expected_artifact_state``/``expected_version``/``expected_updated_at``
        are the caller's own claimed base — reviewer finding 6 (gen-8
        review): ``base_snapshot`` here is always the store's own freshly
        re-read current row, never a value the caller actually supplied, so
        an advance request carrying a stale premise (e.g. "I still believe
        this entry is in draft") committed against whatever the row actually
        was, silently ignoring the caller's now-false belief. The caller
        (``RegistryService.advance_artifact_state``/split_api.py) merges
        these onto ``base_snapshot`` before calling in, so the
        ``compare_and_set`` below already enforces them as part of the CAS
        row-equality check; they are threaded through here only so a
        genuinely divergent replay (the same command_key resubmitted with a
        *different* claimed base) is detected via ``request_digest`` rather
        than silently treated as an identical replay.

        Reviewer finding 5: prior to this, ``advance_artifact_state`` had no
        caller-scoped receipt at all — a retried ``advance`` command_key
        would either re-run the state transition (raising a "forbidden
        transition" error on the second call, since the entry had already
        moved) or silently no-op, with no way for a caller to distinguish
        "my retry landed on the original commit" from "my retry was
        rejected". As with metadata CAS, the receipt row is reserved
        *before* the entry mutation (via ``insert_if_absent``, which holds a
        table-level lock for the rest of this transaction) and a same-key
        replay always returns the entry snapshot exactly as it was
        originally committed under this command_key, never a fresh read of
        whatever the entry has become since under other commands. This is
        not a separate outbox: the receipt and the entry mutation are one
        row each, committed together in one Postgres transaction — a crash
        between "entry written" and "receipt written" cannot happen, and a
        crash after commit but before the HTTP response reaches the caller
        is safe because a replay of the same command_key re-reads this
        already-committed receipt instead of re-running (or failing to
        re-run) the transition.

        ``validate`` (e.g. the caller's "is this transition/lineage legal"
        business-rule check) is invoked only on the genuinely-fresh path,
        *after* the replay short-circuit above has already ruled out a
        replay — never on a replay. This matters because the entry's
        *current* state on a replay is already the post-transition state
        (e.g. "candidate" after a draft->candidate transition already
        committed), so re-running a "is candidate->candidate allowed"
        transition check against it would always (and wrongly) fail.
        """
        # The digest deliberately excludes the entry's current
        # artifact_state: unlike metadata CAS's caller-supplied
        # expected_metadata precondition, there is no separate "expected
        # base state" parameter here — the base is always the freshly-read
        # current state, which legitimately differs between the original
        # call (e.g. "draft") and a replay issued after that call already
        # committed (now "candidate"). Including it would make every
        # genuine replay look divergent. The caller's actual intent is
        # target_state/approver/approval_decision_id for this registry_id.
        request_digest = _request_digest({
            "registry_id": registry_id,
            "target_state": target_state.value if hasattr(target_state, "value") else target_state,
            "approver": approver,
            "approval_decision_id": approval_decision_id,
            # Reviewer finding 6: a same command_key resubmitted with a
            # different claimed base is a divergent request, not an
            # identical replay, even though target_state/approver/
            # approval_decision_id are unchanged.
            "expected_artifact_state": expected_artifact_state,
            "expected_version": expected_version,
            "expected_updated_at": expected_updated_at,
        })
        scoped_key = (
            self.receipt_key(command_key, registry_id, actor=actor, command_type="advance")
            if command_key
            else None
        )

        with self._entries.transaction() as conn:
            reservation: Optional[dict[str, Any]] = None
            if scoped_key:
                # Reviewer finding 2 (gen-10 review): see the identical
                # comment in commit_metadata_cas — always lock entries before
                # receipts in this transaction.
                self._entries.lock_table(conn=conn)
                reservation = {
                    "command_key": command_key,
                    "receipt_key": scoped_key,
                    "registry_id": registry_id,
                    "request_digest": request_digest,
                    "committed_entry": None,
                    "committed_at": None,
                }
                reserved, receipt_payload = self._receipts.insert_if_absent(
                    scoped_key, reservation, conn=conn,
                )
                if not reserved:
                    if receipt_payload.get("request_digest") != request_digest:
                        raise DivergentCommandReplayError(command_key)
                    committed_entry = receipt_payload.get("committed_entry")
                    if committed_entry is None:
                        raise RegistryConcurrentUpdateError(registry_id)
                    return RegistryEntry.from_dict(committed_entry), True

            base_entry = RegistryEntry.from_dict(base_snapshot)
            if validate is not None:
                validate(base_entry)

            entry = base_entry
            entry.artifact_state = target_state
            entry.updated_at = utc_now_iso()
            if approved_at is not None:
                entry.approved_at = approved_at
            if approver:
                entry.approver = approver
            if approval_decision_id:
                entry.approval_decision_id = approval_decision_id
            if actor is not None:
                entry.last_actor = actor
            new_payload = entry.to_dict()

            ok, canonical = self._cas_entry(
                registry_id, base_snapshot, new_payload, conn=conn,
            )
            if not ok:
                raise RegistryConcurrentUpdateError(registry_id)

            if scoped_key:
                finalized = dict(reservation)
                finalized["committed_entry"] = canonical
                finalized["committed_at"] = utc_now_iso()
                filled, _ = self._receipts.compare_and_set(
                    scoped_key, reservation, finalized, conn=conn,
                )
                if not filled:
                    raise RegistryConcurrentUpdateError(registry_id)
        return RegistryEntry.from_dict(canonical), False

    def create_with_receipt(
        self,
        payload_factory: Callable[[], tuple[RegistryEntryCreate, str]],
        *,
        command_key: str,
        actor: Optional[dict[str, Any]] = None,
        unique_fields: tuple[str, ...] = (),
        request_fingerprint: Any = None,
        strategy_id: Optional[str] = None,
        validate_lineage: Optional[Callable[[list[RegistryEntry]], None]] = None,
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create-or-replay a caller-scoped idempotent creation.

        See ``RegistryStore.create_with_receipt`` (storage.py) for the
        reviewer finding 4 rationale. The reservation and the entry
        insertion commit in the same Postgres transaction — mirroring
        :meth:`commit_metadata_cas` — so a same-key replay always returns
        the entry actually committed the first time, never a fresh
        creation, and a crash between "receipt reserved" and "entry
        inserted" cannot leave a receipt pointing at nothing durable (the
        finalize step below fails the whole transaction if it cannot
        complete).

        ``request_fingerprint`` is a JSON-serializable representation of the
        caller's *normalized request* (not the factory-produced entry, which
        embeds a fresh random identity on every call and therefore cannot be
        compared across replays). Reviewer finding 3: a same
        ``Idempotency-Key`` reused with a genuinely different request (e.g.
        a different ``name``) previously returned 200 with the *original*
        entry both times, silently discarding the caller's second, different
        request instead of reporting a conflict. The digest is computed
        before the factory ever runs, so a divergent replay is rejected
        without invoking (and without needing to invoke) the factory a
        second time.
        """
        scoped_key = self.receipt_key(command_key, "register_entry", actor=actor, command_type="create")
        request_digest = _request_digest({"request": request_fingerprint})
        with self._entries.transaction() as conn:
            # Globally consistent lock acquisition order across all writers:
            # 1. Per-strategy_id advisory transaction lock (if strategy_id known up front)
            # 2. Entries table-level lock
            # 3. Receipts table reservation
            # Taking advisory lock first ensures that no wait-for cycle can form
            # between this method and register_strategy_spec_revision.
            if strategy_id:
                self._entries.advisory_xact_lock(strategy_id, conn=conn)
            self._entries.lock_table(conn=conn)
            reservation = {
                "command_key": command_key,
                "receipt_key": scoped_key,
                "request_digest": request_digest,
                "committed_entry": None,
            }
            reserved, receipt_payload = self._receipts.insert_if_absent(
                scoped_key, reservation, conn=conn,
            )
            if not reserved:
                if receipt_payload.get("request_digest") != request_digest:
                    raise DivergentCommandReplayError(command_key)
                committed_entry = receipt_payload.get("committed_entry")
                if committed_entry is None:
                    # Another in-flight transaction reserved this key but has
                    # not yet committed the entry it records.
                    raise RegistryConcurrentUpdateError("register_entry")
                return RegistryEntry.from_dict(committed_entry), True

            payload, registry_id = payload_factory()
            target_strategy_id = strategy_id or getattr(payload, "strategy_id", None)
            if not strategy_id and target_strategy_id:
                self._entries.advisory_xact_lock(target_strategy_id, conn=conn)

            # Re-read existing entries under the advisory and table locks to
            # serialize revision creation and validate lineage against true latest committed state.
            if validate_lineage is not None and target_strategy_id:
                existing_entries = [
                    RegistryEntry.from_dict(raw)
                    for raw in self._list_by_strategy_raw(target_strategy_id, conn=conn)
                ]
                validate_lineage(existing_entries)

            entry = _new_entry(payload, registry_id, actor=actor)
            created, canonical = self._entries.insert_if_absent(
                registry_id, entry.to_dict(), unique_fields=unique_fields, conn=conn,
            )
            if created:
                committed_entry = entry.to_dict()
            else:
                canonical_entry = RegistryEntry.from_dict(canonical)
                if canonical_entry.registry_id != registry_id:
                    from .storage import RegistryUniqueViolationError

                    raise RegistryUniqueViolationError(
                        registry_id, unique_fields, canonical_entry.registry_id,
                    )
                committed_entry = canonical

            finalized = dict(reservation)
            finalized["committed_entry"] = committed_entry
            actual_reg_id = committed_entry.get("registry_id")
            if actual_reg_id:
                finalized["registry_id"] = actual_reg_id
            finalized["committed_at"] = committed_entry.get("created_at") or committed_entry.get("updated_at") or utc_now_iso()
            filled, _ = self._receipts.compare_and_set(scoped_key, reservation, finalized, conn=conn)
            if not filled:
                raise RegistryConcurrentUpdateError("register_entry")
            if actual_reg_id:
                by_reg_id_key = self.receipt_key(command_key, actual_reg_id, actor=actor, command_type="create")
                if by_reg_id_key != scoped_key:
                    by_reg_finalized = dict(finalized)
                    by_reg_finalized["receipt_key"] = by_reg_id_key
                    self._receipts.insert_if_absent(by_reg_id_key, by_reg_finalized, conn=conn)
        self.invalidate_strategy_summary_index_cache(full=False)
        return RegistryEntry.from_dict(committed_entry), False

    def register_strategy_spec_revision(
        self,
        *,
        strategy_id: str,
        registry_id: str,
        payload: RegistryEntryCreate,
        validate_lineage: Callable[[list[RegistryEntry]], None],
        actor: Optional[dict[str, Any]] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[RegistryEntry, bool]:
        """Serialize StrategySpec revision creation per ``strategy_id`` and
        re-validate the version/lineage invariant against the true
        latest-committed state inside the same transaction as the insert.

        Reviewer finding 4 (TOCTOU race): two concurrent requests could each
        read "latest=1.0.0" via a plain, unlocked read before either
        committed, both independently pass the "is this a valid next
        version" check (e.g. both 1.0.1 and 2.0.0 are valid next versions
        from 1.0.0), and both commit — since they target *different*
        versions, the (strategy_id, version, artifact_type) ``unique_fields``
        constraint never catches this at all; the invariant being violated
        is "there is exactly one next revision per current latest", not
        "no two rows share a version". A Postgres session-scoped advisory
        transaction lock keyed on ``strategy_id`` serializes the whole
        read-validate-write sequence for one strategy family: the second
        caller blocks until the first commits and releases the lock (at
        transaction end), then re-reads the true latest-committed version
        and re-validates against *that*, aborting with whatever error
        ``validate_lineage`` raises (mapped to 409/400 by the caller) if the
        version is no longer a valid next step.
        """
        with self._entries.transaction() as conn:
            self._entries.advisory_xact_lock(strategy_id, conn=conn)
            existing_entries = [
                RegistryEntry.from_dict(raw)
                for raw in self._list_by_strategy_raw(strategy_id, conn=conn)
            ]
            validate_lineage(existing_entries)
            entry = _new_entry(payload, registry_id, actor=actor)
            created, canonical = self._entries.insert_if_absent(
                registry_id, entry.to_dict(), unique_fields=unique_fields, conn=conn,
            )
            if created:
                self._store_creation_receipt(registry_id, entry.to_dict(), conn=conn)
        if created:
            self.invalidate_strategy_summary_index_cache(full=False)
            return entry, True
        # See create_if_absent's docstring: returns the live current row;
        # callers use get_creation_receipt() for the immutable-content
        # comparison instead.
        return RegistryEntry.from_dict(canonical), False

    def update_deployment_summary(
        self,
        registry_id: str,
        *,
        current_stage: DeploymentStage,
        deployment_plan_id: Optional[str] = None,
        runtime_binding_id: Optional[str] = None,
        actor: Optional[dict[str, Any]] = None,
    ) -> Optional[RegistryEntry]:
        raw = self._entries.get(registry_id)
        if raw is None:
            return None
        entry = RegistryEntry.from_dict(raw)
        entry.deployment_summary = DeploymentSummary(
            current_stage=current_stage,
            deployment_plan_id=deployment_plan_id,
            runtime_binding_id=runtime_binding_id,
            last_transition_at=utc_now_iso(),
        )
        try:
            return self.update(entry, expected=raw, actor=actor)
        except RegistryConcurrentUpdateError:
            # A concurrent projection update lost a race; report the current
            # durable entry rather than silently dropping the caller's write.
            raise

    # -- Read operations ------------------------------------------------

    def get(self, registry_id: str) -> Optional[RegistryEntry]:
        raw = self._entries.get(registry_id)
        return RegistryEntry.from_dict(raw) if raw is not None else None

    def _list_by_strategy_raw(
        self, strategy_id: str, *, conn: Optional[Any] = None,
    ) -> list[dict[str, Any]]:
        """Read only rows for ``strategy_id`` in SQL without scanning all payloads into Python."""
        rows = self._entries.list_all_selected(
            "(payload)::text",
            visible_sql="payload ->> 'strategy_id' = %s",
            keep_params=(strategy_id,),
            conn=conn,
        )
        return [json.loads(r[0]) for r in rows if r and r[0] is not None]

    def list_by_strategy(self, strategy_id: str) -> list[RegistryEntry]:
        return [
            RegistryEntry.from_dict(raw)
            for raw in self._list_by_strategy_raw(strategy_id)
        ]

    def list_all_entries(self) -> list[RegistryEntry]:
        return [
            RegistryEntry.from_dict(raw)
            for raw in self._entries.list_all()
        ]

    def list_by_artifact_type(self, artifact_type: str) -> list[RegistryEntry]:
        """Type-filtered read in SQL: other artifact types are never fetched or decoded."""
        wanted = str(getattr(artifact_type, "value", artifact_type))
        with self._entries._connect() as conn:
            cursor = conn.execute(artifact_type_select_sql(self._entries.table), (wanted, wanted))
            rows = cursor.fetchall()
        out: list[RegistryEntry] = []
        for row in rows:
            raw = row[0] if isinstance(row, (tuple, list)) else next(iter(row.values()))
            if raw is not None:
                out.append(RegistryEntry.from_dict(json.loads(raw)))
        return out

    def list_persona_binding_rows(
        self, *, visible_tenants: Optional[frozenset] = None, artifact_type: Optional[str] = None,
    ) -> list[dict]:
        """Persona-binding flat rows projected inside Postgres (no entry rebuild).

        The visible owner population is selected first; only strategy families
        with at least one bound visible version are returned (every visible
        version of such a family, bound or not), decided in the same statement
        as the projection.
        """
        visible_sql, family_sql, bound_sql, keep_params = persona_binding_family_keep_sql(
            visible_tenants, artifact_type,
        )
        return persona_binding_rows_from_columns(
            self._entries.list_all_selected(
                persona_binding_select_sql(), visible_sql=visible_sql, family_sql=family_sql,
                bound_sql=bound_sql, keep_params=keep_params,
            )
        )

    def list_lineage_rows(self, *, visible_tenants: Optional[frozenset] = None) -> list[dict]:
        """Flat lineage rows projected inside Postgres (no RegistryEntry rebuild).

        The verified visible owner population is the only filter; the JSON
        leaves read are constant paths, request values travel as parameters.
        """
        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(visible_tenants, None)
        return lineage_rows_from_columns(
            self._entries.list_all_selected(
                lineage_select_sql(), visible_sql=visible_sql, keep_params=params,
            )
        )

    def list_strategy_artifact_rows(
        self, *, visible_tenants: Optional[frozenset] = None,
    ) -> list[dict]:
        """Flat execution_bundle catalog rows projected inside Postgres (no metadata decoded)."""
        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(
            visible_tenants, "execution_bundle",
        )
        return strategy_artifact_rows_from_columns(
            self._entries.list_all_selected(
                strategy_artifact_select_sql(), visible_sql=visible_sql, keep_params=params,
            )
        )

    def _ensure_artifact_catalog_indexes(self) -> None:
        """Forward-only, additive indexes for the ordered catalog page.

        ``CREATE INDEX IF NOT EXISTS`` changes no row, column or constraint, so
        the previous release keeps reading and writing the same table unchanged.
        """
        table = self._entries.table
        base = self._entries.table_name.split(".")[-1][:40]
        stmts = (
            f"CREATE INDEX IF NOT EXISTS {quote_pg_identifier(base + '_artifact_catalog_idx')} "
            f"ON {table} ({_ARTIFACT_CATALOG_CREATED_SQL} DESC, {_ARTIFACT_CATALOG_ID_SQL} DESC) "
            f"WHERE {_ARTIFACT_CATALOG_PRED_SQL}",
            f"CREATE INDEX IF NOT EXISTS {quote_pg_identifier(base + '_legacy_str_payload_idx')} "
            f"ON {table} ((jsonb_typeof(payload))) WHERE jsonb_typeof(payload) = 'string'",
        )
        with self._entries._connect() as conn:
            for stmt in stmts:
                try:
                    conn.execute(stmt)
                except Exception as exc:
                    if getattr(exc, "sqlstate", "") not in ("23505", "42P07"):
                        raise
                    if hasattr(conn, "rollback"):
                        conn.rollback()

    def _ensure_artifact_type_index(self) -> None:
        """Forward-only, additive expression index for ``list_by_artifact_type``.

        Same pattern as ``_ensure_artifact_catalog_indexes``: ``CREATE INDEX IF
        NOT EXISTS`` changes no row, column or constraint, runs once per table
        (later bootstraps are a catalog no-op) and the previous release never
        reads it.
        """
        table = self._entries.table
        base = self._entries.table_name.split(".")[-1][:40]
        stmt = (
            f"CREATE INDEX IF NOT EXISTS {quote_pg_identifier(base + ARTIFACT_TYPE_INDEX_SUFFIX)} "
            f"ON {table} ({ARTIFACT_TYPE_INDEX_SQL})"
        )
        with self._entries._connect() as conn:
            try:
                conn.execute(stmt)
            except Exception as exc:
                if getattr(exc, "sqlstate", "") not in ("23505", "42P07"):
                    raise
                if hasattr(conn, "rollback"):
                    conn.rollback()

    def _summary_index_identifiers(self) -> tuple[str, str, str, str, str]:
        table_name = getattr(self._entries, "table_name", "registry.entries")
        schema = getattr(self._entries, "schema", None)
        if schema is None:
            schema = table_name.split(".", 1)[0] if "." in table_name else ""
        base = table_name.split(".")[-1][:34]
        prefix = f"{quote_pg_identifier(schema)}." if schema else ""
        seq_ident = f"{prefix}{quote_pg_identifier('pantheon_registry_summary_rev_seq')}"
        fn_build = f"{prefix}{quote_pg_identifier('pantheon_registry_summary_index_build')}"
        fn_tg = f"{prefix}{quote_pg_identifier('pantheon_registry_summary_index_tg_fn')}"
        tg_name = quote_pg_identifier(base + SUMMARY_INDEX_TRIGGER_SUFFIX)
        idx_name = quote_pg_identifier(base + SUMMARY_INDEX_SUFFIX)
        return seq_ident, fn_build, fn_tg, tg_name, idx_name

    def _ensure_strategy_summary_index(self) -> None:
        """Ensure stored ``summary_index``/``summary_rev`` columns, write trigger, partial index, and bounded backfill."""
        self._init_summary_index_cache_state()
        table = self._entries.table
        table_name = getattr(self._entries, "table_name", "registry.entries")
        seq_ident, fn_build, fn_tg, tg_name, idx_name = self._summary_index_identifiers()
        stmts = (
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {SUMMARY_INDEX_COLUMN} JSONB",
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {SUMMARY_REV_COLUMN} BIGINT",
            f"CREATE SEQUENCE IF NOT EXISTS {seq_ident}",
            _strategy_summary_index_build_function_sql(fn_build),
            _strategy_summary_index_trigger_function_sql(fn_tg, fn_build, seq_ident),
            (
                f"CREATE OR REPLACE TRIGGER {tg_name} "
                f"BEFORE INSERT OR UPDATE OF payload ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {fn_tg}()"
            ),
            (
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON {table} ({SUMMARY_REV_COLUMN} ASC) "
                f"WHERE {SUMMARY_INDEX_COLUMN} IS NOT NULL"
            ),
        )
        with self._entries._connect() as conn:
            if hasattr(self._entries, "advisory_xact_lock"):
                self._entries.advisory_xact_lock(f"{table_name}:summary_index", conn=conn)
            for stmt in stmts:
                try:
                    conn.execute(stmt)
                except Exception as exc:
                    if getattr(exc, "sqlstate", "") not in ("23505", "42P07", "42701", "42710"):
                        raise
                    if hasattr(conn, "rollback"):
                        conn.rollback()
        self._summary_index_ready = True
        self.backfill_strategy_summary_index()

    def backfill_strategy_summary_index(
        self,
        *,
        batch_size: int = SUMMARY_INDEX_BACKFILL_BATCH_SIZE,
        max_batches: int = SUMMARY_INDEX_BACKFILL_MAX_BATCHES,
    ) -> dict[str, Any]:
        """Idempotent, bounded batched backfill of ``summary_index`` and ``summary_rev`` for pre-existing ``strategy_spec`` rows."""
        self._init_summary_index_cache_state()
        table = self._entries.table
        seq_ident, fn_build, _fn_tg, _tg_name, _idx_name = self._summary_index_identifiers()
        effective_batch = max(1, int(batch_size))
        effective_max_batches = max(1, int(max_batches))
        batch_sql = (
            "WITH todo AS ("
            f"SELECT record_id FROM {table} "
            f"WHERE {SUMMARY_INDEX_COLUMN} IS NULL AND ("
            f"{ARTIFACT_TYPE_INDEX_SQL} = 'strategy_spec' "
            f"OR (jsonb_typeof(payload) = 'string' AND {fn_build}(payload) IS NOT NULL)"
            ") ORDER BY record_id ASC LIMIT %s FOR UPDATE SKIP LOCKED"
            ") "
            f"UPDATE {table} AS t "
            f"SET {SUMMARY_INDEX_COLUMN} = {fn_build}(t.payload), "
            f"{SUMMARY_REV_COLUMN} = nextval('{seq_ident}') "
            "FROM todo WHERE t.record_id = todo.record_id"
        )
        t_start = time.perf_counter()
        total_updated = 0
        batch_elapsed_ms: list[float] = []
        for _ in range(effective_max_batches):
            t_b0 = time.perf_counter()
            with self._entries._connect() as conn:
                cur = conn.execute(batch_sql, (effective_batch,))
                updated = int(getattr(cur, "rowcount", 0) or 0)
            elapsed_ms = (time.perf_counter() - t_b0) * 1000.0
            batch_elapsed_ms.append(round(elapsed_ms, 3))
            if updated <= 0:
                break
            total_updated += updated
            if updated < effective_batch:
                break
        total_ms = round((time.perf_counter() - t_start) * 1000.0, 3)
        report = {
            "batch_size": effective_batch,
            "max_batches": effective_max_batches,
            "batches_executed": len(batch_elapsed_ms),
            "rows_updated": total_updated,
            "batch_elapsed_ms": batch_elapsed_ms,
            "max_batch_lock_ms": max(batch_elapsed_ms) if batch_elapsed_ms else 0.0,
            "total_elapsed_ms": total_ms,
        }
        self.last_summary_index_backfill = report
        if total_updated > 0:
            self.invalidate_strategy_summary_index_cache(full=False)
        return report

    def list_strategy_artifact_page(
        self,
        *,
        visible_tenants: Optional[frozenset] = None,
        artifact_state: Optional[str] = None,
        strategy_id: Optional[str] = None,
        limit: int,
        cursor: Optional[tuple[str, tuple]] = None,
    ) -> tuple[list[dict], int, Optional[str]]:
        """One bounded ordered catalog page read in Postgres: ``(items, exact_total, next_page_token)``.

        Rows come back as ``(created_at, registry_id)`` descending and are cut at
        ``limit + 1`` in SQL; the total is a separate ``count(*)`` over the same
        predicate, so rows fetched per page do not depend on the population.
        Legacy JSON-string payload rows (not indexable) route to the unbounded
        path so the item set is unchanged.
        """
        entries = self._entries
        if not hasattr(entries, "_use_conn") or self._has_legacy_string_payloads():
            return page_strategy_artifact_rows(
                self.list_strategy_artifact_rows(visible_tenants=visible_tenants),
                artifact_state=artifact_state, strategy_id=strategy_id, limit=limit, cursor=cursor,
            )
        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(visible_tenants, None)
        where = [_ARTIFACT_CATALOG_PRED_SQL, f"({visible_sql})"]
        args: list[Any] = list(params)
        if artifact_state is not None:
            where.append("payload ->> 'artifact_state' = %s")
            args.append(artifact_state)
        if strategy_id is not None:
            where.append("payload ->> 'strategy_id' = %s")
            args.append(strategy_id)
        base_where = " AND ".join(where)
        page_where, page_args = base_where, list(args)
        offset_sql = ""
        if cursor is not None:
            kind, value = cursor
            if kind == "after":
                page_where += f" AND ({_ARTIFACT_CATALOG_CREATED_SQL}, {_ARTIFACT_CATALOG_ID_SQL}) < (%s::text, %s::text)"
                page_args += [value[0], value[1]]
            else:
                offset_sql = " OFFSET %s"
                page_args.append(int(value[0]))
        page_sql = (
            f"SELECT {strategy_artifact_select_sql()} FROM {entries.table} WHERE {page_where} "
            f"ORDER BY {_ARTIFACT_CATALOG_CREATED_SQL} DESC, {_ARTIFACT_CATALOG_ID_SQL} DESC "
            f"LIMIT {int(limit) + 1}{offset_sql}"
        )
        count_sql = f"SELECT count(*) FROM {entries.table} WHERE {base_where}"
        with entries._use_conn(None) as conn:
            cur = conn.execute(page_sql, tuple(page_args))
            raw = [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]
            cur = conn.execute(count_sql, tuple(args))
            first = cur.fetchone()
        total = int((first[0] if not isinstance(first, dict) else next(iter(first.values()))) if first else 0)
        rows = strategy_artifact_rows_from_columns(raw)
        has_more = len(rows) > limit
        items = rows[:limit]
        token = (
            encode_strategy_artifact_page_token(strategy_artifact_sort_key(items[-1]))
            if has_more and items else None
        )
        return items, total, token

    def _strategy_spec_keyset_rows(
        self,
        *,
        select_sql: str,
        visible_tenants: Optional[frozenset],
        predicates: Sequence[str],
        args: Sequence[Any],
        limit: int,
        after_registry_id: Optional[str],
        key_sql: str = "payload ->> 'registry_id'",
        source_sql: Optional[str] = None,
    ) -> list[str]:
        """Shared ``registry_id`` keyset page over StrategySpec rows, cut in SQL.

        ``select_sql``/``predicates``/``key_sql``/``source_sql`` are code-defined
        (never request input); the visible tenant population, request values and
        the cursor travel as bound parameters. Only ``limit`` rows leave Postgres.
        """
        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(visible_tenants, "strategy_spec")
        where = ["jsonb_typeof(payload) = 'object'", f"({visible_sql})", *predicates]
        bound: list[Any] = list(params) + list(args)
        if after_registry_id:
            where.append(f"{key_sql} > %s")
            bound.append(after_registry_id)
        # The page is cut on the key first; ``select_sql`` is built only for the kept rows.
        sql = (
            f"SELECT {select_sql} FROM (SELECT payload FROM {source_sql or self._entries.table} "
            f"WHERE {' AND '.join(where)} ORDER BY {key_sql} ASC LIMIT {int(limit)}) AS page "
            f"ORDER BY {key_sql} ASC"
        )
        with self._entries._use_conn(None) as conn:
            rows = conn.execute(sql, tuple(bound)).fetchall()
        return [r[0] if not isinstance(r, dict) else next(iter(r.values())) for r in rows]

    def list_card_admission_drafts(
        self,
        *,
        visible_tenants: Optional[frozenset] = None,
        tenant_id: str,
        limit: int,
        after_registry_id: Optional[str] = None,
    ) -> list[RegistryEntry]:
        """At most ``limit`` admissible card drafts, filtered inside Postgres (never the draft table).

        Predicate: strategy_spec, draft, ``metadata.strategy_spec.metadata.card_wiring.status
        == 'gate_ready'`` and ``metadata.tenant_id``; ordered by ``registry_id`` with a keyset
        cursor. Non-matching drafts are neither fetched nor decoded. Legacy JSON-string payloads
        are not card drafts and are excluded by the object-type guard.
        """
        rows = self._strategy_spec_keyset_rows(
            select_sql="(payload)::text",
            visible_tenants=visible_tenants,
            predicates=(
                "payload ->> 'artifact_state' = 'draft'",
                "payload #>> '{metadata,strategy_spec,metadata,card_wiring,status}' = 'gate_ready'",
                "payload #>> '{metadata,tenant_id}' = %s",
            ),
            args=(tenant_id,),
            limit=limit,
            after_registry_id=after_registry_id,
        )
        return [RegistryEntry.from_dict(json.loads(r)) for r in rows]

    def list_discovery_candidates(
        self,
        *,
        visible_tenants: Optional[frozenset] = None,
        limit: int,
        after_registry_id: Optional[str] = None,
    ) -> list[dict]:
        """At most ``limit`` discovery rows (REGISTRY-DISCOVERY-SPEC-PAGE-20261125).

        Same keyset as :meth:`list_card_admission_drafts`; predicate: strategy_spec,
        ``artifact_state`` not retired/rejected, visible owner tenant. Only the five
        discovery fields are built in SQL, so storage refs, evidence and audit fields
        never leave Postgres. Legacy JSON-string payloads are unwrapped first so the
        row set equals the unpaged list; the key is byte ordered (``COLLATE "C"``) to
        match the in-memory owner.
        """
        excluded = ", ".join(f"'{s}'" for s in DISCOVERY_EXCLUDED_STATES)
        rows = self._strategy_spec_keyset_rows(
            select_sql=_DISCOVERY_SELECT_SQL,
            visible_tenants=visible_tenants,
            predicates=(f"payload ->> 'artifact_state' NOT IN ({excluded})",),
            args=(),
            limit=limit,
            after_registry_id=after_registry_id,
            key_sql="(payload ->> 'registry_id') COLLATE \"C\"",
            # Same unwrap as ``PostgresJsonOwnerStore.list_all_selected``: ``payload || '{}'``
            # detoasts each wide row once (about 3x faster per page than one detoast per leaf).
            source_sql=(
                "(SELECT CASE WHEN jsonb_typeof(u) = 'array' THEN "
                "(CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb ELSE u END) "
                "ELSE u END AS payload "
                f"FROM (SELECT payload, payload || '{{}}'::jsonb AS u FROM {self._entries.table} OFFSET 0) AS raw "
                "OFFSET 0) AS discovery_rows"
            ),
        )
        return [discovery_candidate_from_payload(json.loads(r)) for r in rows]

    def _has_legacy_string_payloads(self) -> bool:
        with self._entries._use_conn(None) as conn:
            cur = conn.execute(
                f"SELECT 1 FROM {self._entries.table} WHERE jsonb_typeof(payload) = 'string' LIMIT 1"
            )
            return cur.fetchone() is not None

    def list_strategy_spec_summary_rows(
        self, *, visible_tenants: Optional[frozenset] = None, artifact_type: Optional[str] = "strategy_spec",
    ) -> list[dict]:
        """Strategy-spec summary rows projected inside Postgres (no RegistryEntry rebuild)."""
        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(
            visible_tenants, artifact_type,
        )
        rows = strategy_spec_summary_rows_from_columns(
            self._entries.list_all_selected(
                strategy_spec_summary_select_sql(), visible_sql=visible_sql, keep_params=params,
            )
        )
        extracted_ids = tuple(
            str(
                r.get("registry_id")
                or (r.get("metadata") if isinstance(r.get("metadata"), dict) else {}).get("spec_version_id")
                or ""
            ).strip()
            for r in rows
        )
        self.full_summary_rows_extracted = len(rows)
        self.full_summary_row_ids_extracted = extracted_ids
        return rows

    def list_strategy_spec_summary_index_rows(
        self,
        *,
        visible_tenants: Optional[frozenset] = None,
        artifact_type: Optional[str] = "strategy_spec",
        include_title: bool = False,
        include_scope: bool = False,
    ) -> list[dict]:
        """Lightweight candidate-index rows for Phase 1 of strategy_summary_page.

        Reads pre-extracted candidate-index fields from the stored ``summary_index``
        column on ``registry.entries`` (never evaluating ``payload`` JSON expressions
        across ``strategy_spec`` rows on each request) paired with a stamp-validated
        in-process cache checked by one cheap index-only query on ``summary_rev``.
        """
        self._init_summary_index_cache_state()
        self.full_summary_rows_extracted = 0
        self.full_summary_row_ids_extracted = ()
        if (
            artifact_type == "strategy_spec"
            and hasattr(self._entries, "_use_conn")
            and hasattr(self._entries, "table")
        ):
            if not self._summary_index_ready and hasattr(self._entries, "_connect"):
                self._ensure_strategy_summary_index()
            if not self._summary_index_cache_enabled:
                sql, sql_params = strategy_spec_summary_index_stored_tenant_select_sql(
                    self._entries.table, visible_tenants,
                )
                with self._entries._use_conn(None) as conn:
                    cur = conn.execute(sql, sql_params) if sql_params else conn.execute(sql)
                    raw_rows = [
                        tuple(r.values()) if isinstance(r, dict) else tuple(r)
                        for r in cur.fetchall()
                    ]
                out = _CompiledIndexRowList(
                    project_cached_summary_index_row(
                        _decode_stored_summary_index_row(raw_json),
                        include_title=include_title,
                        include_scope=include_scope,
                    )
                    for _rec_id, _rev, raw_json in raw_rows
                    if raw_json is not None
                )
                self.phase1_index_sql_reads += 1
                self.phase1_index_rows_fetched = len(out)
                return out

            with self._summary_index_lock:
                table = self._entries.table
                need_full_reload = self._summary_index_by_record_id is None
                if not need_full_reload:
                    with self._entries._use_conn(None) as conn:
                        stamp_cur = conn.execute(strategy_spec_summary_index_stamp_sql(table))
                        stamp_row = stamp_cur.fetchone()
                    if isinstance(stamp_row, dict):
                        vals = tuple(stamp_row.values())
                    else:
                        vals = tuple(stamp_row) if stamp_row is not None else (0, 0, 0)
                    stamp = (int(vals[0] or 0), int(vals[1] or 0), int(vals[2] or 0))
                    if stamp == self._summary_index_stamp:
                        self.phase1_index_cache_hits += 1
                        self.phase1_index_rows_fetched = 0
                    else:
                        db_count, db_max_rev, _db_sum_rev = stamp
                        prev_stamp = self._summary_index_stamp
                        if (
                            db_count > 0
                            and prev_stamp is not None
                            and prev_stamp[1] > 0
                            and db_max_rev > prev_stamp[1]
                        ):
                            with self._entries._use_conn(None) as conn:
                                delta_cur = conn.execute(
                                    strategy_spec_summary_index_delta_sql(table),
                                    (prev_stamp[1],),
                                )
                                delta_raw = [
                                    tuple(r.values()) if isinstance(r, dict) else tuple(r)
                                    for r in delta_cur.fetchall()
                                ]
                            by_id = self._summary_index_by_record_id
                            assert by_id is not None
                            running_count = len(by_id)
                            running_max_rev = prev_stamp[1]
                            running_sum_rev = prev_stamp[2]
                            seen_in_delta: dict[str, int] = {}
                            for rec_id, rev_val, raw_json in delta_raw:
                                if raw_json is None:
                                    continue
                                rec_key = str(rec_id)
                                rev_int = int(rev_val or 0)
                                if rec_key in seen_in_delta:
                                    running_sum_rev += rev_int - seen_in_delta[rec_key]
                                elif rec_key in by_id:
                                    running_sum_rev += rev_int - by_id[rec_key][0]
                                else:
                                    running_count += 1
                                    running_sum_rev += rev_int
                                seen_in_delta[rec_key] = rev_int
                                if rev_int > running_max_rev:
                                    running_max_rev = rev_int
                            if delta_raw and (running_count, running_max_rev, running_sum_rev) == stamp:
                                for rec_id, rev_val, raw_json in delta_raw:
                                    if raw_json is None:
                                        continue
                                    rec_key = str(rec_id)
                                    by_id.pop(rec_key, None)
                                    by_id[rec_key] = (
                                        int(rev_val or 0),
                                        _decode_stored_summary_index_row(raw_json),
                                    )
                                self._summary_index_stamp = stamp
                                self._summary_index_tenant_views.clear()
                                self.phase1_index_delta_reads += 1
                                self.phase1_index_rows_fetched = len(delta_raw)
                            else:
                                need_full_reload = True
                        else:
                            need_full_reload = True

                if need_full_reload:
                    with self._entries._use_conn(None) as conn:
                        full_cur = conn.execute(strategy_spec_summary_index_stored_select_sql(table))
                        full_raw = [
                            tuple(r.values()) if isinstance(r, dict) else tuple(r)
                            for r in full_cur.fetchall()
                        ]
                    new_by_id: dict[str, tuple[int, _CompiledIndexRow]] = {}
                    actual_max_rev = 0
                    actual_sum_rev = 0
                    for rec_id, rev_val, raw_json in full_raw:
                        if raw_json is None:
                            continue
                        rec_key = str(rec_id)
                        rev_int = int(rev_val or 0)
                        new_by_id[rec_key] = (rev_int, _decode_stored_summary_index_row(raw_json))
                        actual_sum_rev += rev_int
                        if rev_int > actual_max_rev:
                            actual_max_rev = rev_int
                    self._summary_index_by_record_id = new_by_id
                    self._summary_index_stamp = (len(new_by_id), actual_max_rev, actual_sum_rev)
                    self._summary_index_tenant_views.clear()
                    self.phase1_index_sql_reads += 1
                    self.phase1_index_rows_fetched = len(new_by_id)

                tenant_key = frozenset(visible_tenants) if visible_tenants is not None else None
                view_key = (tenant_key, bool(include_title), bool(include_scope))
                cached_view = self._summary_index_tenant_views.pop(view_key, None)
                if cached_view is not None:
                    self._summary_index_tenant_views[view_key] = cached_view
                    return cached_view
                assert self._summary_index_by_record_id is not None
                view_rows = _CompiledIndexRowList(
                    project_cached_summary_index_row(
                        row, include_title=include_title, include_scope=include_scope,
                    )
                    for _rev, row in self._summary_index_by_record_id.values()
                    if tenant_key is None or row._owner_tenant in tenant_key
                )
                if len(self._summary_index_tenant_views) >= SUMMARY_INDEX_MAX_TENANT_VIEWS:
                    oldest_key = next(iter(self._summary_index_tenant_views))
                    self._summary_index_tenant_views.pop(oldest_key, None)
                self._summary_index_tenant_views[view_key] = view_rows
                return view_rows

        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(
            visible_tenants, artifact_type,
        )
        if hasattr(self._entries, "_use_conn") and hasattr(self._entries, "table"):
            sql = _strategy_spec_summary_index_single_pass_sql(
                self._entries.table, visible_sql, include_title=include_title,
                include_scope=include_scope,
            )
            # The visible predicate is bound twice: once in the table scan
            # (before any payload expression) and once over the normalised row.
            sql_params = params + params
            with self._entries._use_conn(None) as conn:
                cursor = conn.execute(sql, sql_params) if sql_params else conn.execute(sql)
                raw_rows = [
                    tuple(r.values()) if isinstance(r, dict) else tuple(r)
                    for r in cursor.fetchall()
                ]
            self.phase1_payload_rescans += 1
            return strategy_spec_summary_index_rows_from_columns(
                raw_rows, include_title=include_title, include_scope=include_scope,
            )
        return strategy_spec_summary_index_rows_from_columns(
            self._entries.list_all_selected(
                strategy_spec_summary_index_select_sql(
                    include_title=include_title, include_scope=include_scope,
                ),
                visible_sql=visible_sql,
                keep_params=params,
            ),
            include_title=include_title,
            include_scope=include_scope,
        )

    def get_strategy_spec_summary_rows_by_ids(
        self,
        registry_ids: Sequence[str],
        *,
        visible_tenants: Optional[frozenset] = None,
        artifact_type: Optional[str] = "strategy_spec",
    ) -> dict[str, dict]:
        """Extract full summary fields inside Postgres only for the requested ``registry_ids`` (Phase 2)."""
        target_ids = [str(rid).strip() for rid in (registry_ids or ()) if str(rid).strip()]
        if not target_ids:
            self.full_summary_rows_extracted = 0
            self.full_summary_row_ids_extracted = ()
            return {}
        unique_ids = list(dict.fromkeys(target_ids))
        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(
            visible_tenants, artifact_type,
        )
        if hasattr(self._entries, "_use_conn") and hasattr(self._entries, "table"):
            cols_sql = strategy_spec_summary_select_sql()
            by_pk_sql = (
                f"SELECT {cols_sql} FROM ("
                "SELECT updated_at, CASE WHEN jsonb_typeof(u) = 'array' THEN "
                "(CASE WHEN jsonb_typeof(payload) = 'string' "
                "THEN (payload #>> '{}')::jsonb ELSE u END) ELSE u END AS payload "
                f"FROM (SELECT updated_at, payload, payload || '{{}}'::jsonb AS u "
                f"FROM {self._entries.table} WHERE record_id = ANY(%s::text[]) OFFSET 0) AS raw OFFSET 0"
                f") AS rows WHERE jsonb_typeof(payload) = 'object' AND ({visible_sql}) "
                "ORDER BY updated_at ASC"
            )
            with self._entries._use_conn(None) as conn:
                cursor = conn.execute(by_pk_sql, (unique_ids, *params))
                raw_rows = [
                    tuple(r.values()) if isinstance(r, dict) else tuple(r)
                    for r in cursor.fetchall()
                ]
                decoded = strategy_spec_summary_rows_from_columns(raw_rows)
                out: dict[str, dict] = {}
                for row in decoded:
                    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                    rid = str(row.get("registry_id") or meta.get("spec_version_id") or "").strip()
                    if rid:
                        out[rid] = row
                missing_ids = [rid for rid in unique_ids if rid not in out]
                if missing_ids:
                    fallback_sql = (
                        f"SELECT {cols_sql} FROM ("
                        "SELECT updated_at, CASE WHEN jsonb_typeof(u) = 'array' THEN "
                        "(CASE WHEN jsonb_typeof(payload) = 'string' "
                        "THEN (payload #>> '{}')::jsonb ELSE u END) ELSE u END AS payload "
                        f"FROM (SELECT updated_at, payload, payload || '{{}}'::jsonb AS u "
                        f"FROM {self._entries.table} OFFSET 0) AS raw OFFSET 0"
                        f") AS rows WHERE jsonb_typeof(payload) = 'object' AND ({visible_sql}) "
                        "AND COALESCE(payload ->> 'registry_id', payload #>> '{metadata,spec_version_id}') = ANY(%s::text[]) "
                        "ORDER BY updated_at ASC"
                    )
                    fb_cursor = conn.execute(fallback_sql, (*params, missing_ids))
                    fb_raw = [
                        tuple(r.values()) if isinstance(r, dict) else tuple(r)
                        for r in fb_cursor.fetchall()
                    ]
                    for row in strategy_spec_summary_rows_from_columns(fb_raw):
                        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                        rid = str(row.get("registry_id") or meta.get("spec_version_id") or "").strip()
                        if rid and rid not in out:
                            out[rid] = row
        else:
            id_filter_sql = (
                f"({visible_sql}) AND COALESCE(payload ->> 'registry_id', "
                "payload #>> '{metadata,spec_version_id}') = ANY(%s::text[])"
            )
            decoded = strategy_spec_summary_rows_from_columns(
                self._entries.list_all_selected(
                    strategy_spec_summary_select_sql(),
                    visible_sql=id_filter_sql,
                    keep_params=(*params, unique_ids),
                )
            )
            out = {}
            for row in decoded:
                meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                rid = str(row.get("registry_id") or meta.get("spec_version_id") or "").strip()
                if rid:
                    out[rid] = row

        self.full_summary_rows_extracted = len(out)
        self.full_summary_row_ids_extracted = tuple(rid for rid in target_ids if rid in out)
        return out

    def list_regime_strategy_spec_rows(
        self,
        *,
        visible_tenants: Optional[frozenset] = None,
    ) -> list[dict]:
        """Projected non-retired visible StrategySpec rows for POST /api/registry/regime/evaluate.

        Filters ``artifact_type = 'strategy_spec'``, ``artifact_state <> 'retired'``,
        and ``visible_tenants`` in SQL (unwrapping legacy JSON-string payloads),
        and projects only the top-level identity/version/tenant fields plus the
        metadata leaves inspected by ``services.registry.regime.profiles.profile_for``.
        """
        visible_sql, _fam, _bound, params = persona_binding_family_keep_sql(
            visible_tenants, "strategy_spec",
        )
        pred_sql = f"({visible_sql}) AND COALESCE(payload ->> 'artifact_state', '') <> 'retired'"
        if hasattr(self._entries, "_use_conn") and hasattr(self._entries, "table"):
            sql = _regime_strategy_spec_single_pass_sql(self._entries.table, pred_sql)
            sql_params = params + params
            with self._entries._use_conn(None) as conn:
                cursor = conn.execute(sql, sql_params) if sql_params else conn.execute(sql)
                raw_rows = [
                    tuple(r.values()) if isinstance(r, dict) else tuple(r)
                    for r in cursor.fetchall()
                ]
            return regime_strategy_spec_rows_from_columns(raw_rows)
        return regime_strategy_spec_rows_from_columns(
            self._entries.list_all_selected(
                regime_strategy_spec_select_sql(),
                visible_sql=pred_sql,
                keep_params=params,
            )
        )

    def list_regime_model_artifact_rows(self) -> list[dict]:
        """Projected non-retired ModelArtifact rows for POST /api/registry/regime/evaluate."""
        pred_sql = (
            "payload ->> 'artifact_type' = %s "
            "AND COALESCE(payload ->> 'artifact_state', '') <> 'retired'"
        )
        params = ("model_artifact",)
        if hasattr(self._entries, "_use_conn") and hasattr(self._entries, "table"):
            sql = _regime_model_artifact_single_pass_sql(self._entries.table, pred_sql)
            sql_params = params + params
            with self._entries._use_conn(None) as conn:
                cursor = conn.execute(sql, sql_params)
                raw_rows = [
                    tuple(r.values()) if isinstance(r, dict) else tuple(r)
                    for r in cursor.fetchall()
                ]
            return regime_model_artifact_rows_from_columns(raw_rows)
        return regime_model_artifact_rows_from_columns(
            self._entries.list_all_selected(
                regime_model_artifact_select_sql(),
                visible_sql=pred_sql,
                keep_params=params,
            )
        )

    def resolve_latest_approved(self, strategy_id: str) -> Optional[RegistryEntry]:
        entries = self.list_by_strategy(strategy_id)
        approved = [e for e in entries if e.artifact_state.value == "approved"]
        if not approved:
            return None

        def _parse_ver(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split("."))

        approved.sort(key=lambda e: _parse_ver(e.version), reverse=True)
        return approved[0]


def _binding_leaf_paths() -> list[tuple[str, ...]]:
    """Leaf paths read for a persona-binding row, in assembly order."""
    meta = ("metadata",)
    spec = ("metadata", "strategy_spec")
    spec_meta = spec + ("metadata",)
    return (
        [(k,) for k in PERSONA_BINDING_ROW_KEYS]
        + [meta + (k,) for k in PERSONA_BINDING_META_KEYS]
        + [meta + ("provenance", "created_at")]
        + [spec + (k,) for k in PERSONA_BINDING_SPEC_KEYS]
        + [spec + ("provenance", "created_at")]
        + [spec_meta + (k,) for k in PERSONA_BINDING_INNER_META_KEYS]
    )


def _sql_node(path: tuple[str, ...]) -> str:
    return "payload #> '{" + ",".join(path) + "}'"


def persona_binding_select_sql() -> str:
    """Plain column list over ``payload``: one jsonb leaf (as text) per binding field.

    No jsonb construction in SQL. A missing leaf is SQL ``NULL``; a JSON ``null``
    leaf is the text ``null``, so presence is known without a second lookup and
    no field value can collide with it. The two container-shape facts Python
    needs (is ``strategy_spec`` / ``strategy_spec.metadata`` an object) are two
    trailing columns. Built only from constant key tuples (no request input).
    """
    cols = ", ".join(f"({_sql_node(p)})::text" for p in _binding_leaf_paths())
    shapes = ", ".join(
        f"jsonb_typeof({_sql_node(p)})"
        for p in (("metadata", "strategy_spec"), ("metadata", "strategy_spec", "metadata"))
    )
    return f"{cols}, {shapes}"


_EMPTY_JSONB_VALUES = "'null'::jsonb, '[]'::jsonb, '{}'::jsonb, '\"\"'::jsonb, 'false'::jsonb, '0'::jsonb"
PERSONA_BINDING_FAMILY_SQL = "payload ->> 'strategy_id'"
PERSONA_BINDING_BOUND_SQL = (
    "("
    + " OR ".join(
        f"COALESCE({_sql_node(p)}, 'null'::jsonb) NOT IN ({_EMPTY_JSONB_VALUES})"
        for p in (
            ("metadata", "strategy_spec", "persona_ids"),
            ("metadata", "personaIds"),
            ("metadata", "persona_ids"),
            ("metadata", "persona_id"),
            ("metadata", "strategy_spec", "metadata", "persona_id"),
        )
    )
    + ")"
)


def persona_binding_family_keep_sql(
    visible_tenants: Optional[frozenset] = None, artifact_type: Optional[str] = None,
) -> tuple[str, str, str, tuple]:
    """``(visible, family, bound, params)`` SQL facts for the visible-then-family row selection.

    The verified visible owner population (``visible_tenants`` / ``artifact_type``)
    is applied first; family eligibility is then decided only over those rows, in
    the same statement. Mirrors ``storage.persona_binding_has_binding`` (a version
    is bound when any persona-id source is truthy). A visible row without a
    ``strategy_id`` is always kept. Request-derived values are bound parameters;
    paths are constant; over-inclusion of the binding test is the safe direction.
    """
    visible, params = [], []
    if artifact_type is not None:
        visible.append("payload ->> 'artifact_type' = %s")
        params.append(artifact_type)
    if visible_tenants is not None:
        named = sorted(t for t in visible_tenants if t is not None)
        clauses = ["payload ->> 'owner_tenant' = ANY(%s::text[])"]
        params.append(named)
        if None in visible_tenants:
            clauses.append("payload ->> 'owner_tenant' IS NULL")
        visible.append("(" + " OR ".join(clauses) + ")")
    visible_sql = " AND ".join(visible) or "TRUE"
    return visible_sql, PERSONA_BINDING_FAMILY_SQL, PERSONA_BINDING_BOUND_SQL, tuple(params)


def persona_binding_rows_from_columns(rows) -> list[dict]:
    """Assemble ``persona_binding_row``-shaped dicts from the select columns."""
    n = len(_binding_leaf_paths())
    i_meta = len(PERSONA_BINDING_ROW_KEYS)
    i_prov = i_meta + len(PERSONA_BINDING_META_KEYS)
    i_spec = i_prov + 1
    i_spec_prov = i_spec + len(PERSONA_BINDING_SPEC_KEYS)
    i_inner = i_spec_prov + 1
    out: list[dict] = []
    for r in rows:
        def val(i):
            return json.loads(r[i])

        def pick(keys, start):
            return {k: val(start + j) for j, k in enumerate(keys) if r[start + j] is not None}

        row = pick(PERSONA_BINDING_ROW_KEYS, 0)
        meta = pick(PERSONA_BINDING_META_KEYS, i_meta)
        if r[i_prov] is not None:
            meta["provenance"] = {"created_at": val(i_prov)}
        if r[n] == "object":
            spec = pick(PERSONA_BINDING_SPEC_KEYS, i_spec)
            if r[i_spec_prov] is not None:
                spec["provenance"] = {"created_at": val(i_spec_prov)}
            if r[n + 1] == "object":
                spec["metadata"] = pick(PERSONA_BINDING_INNER_META_KEYS, i_inner)
            meta["strategy_spec"] = spec
        row["metadata"] = meta
        out.append(row)
    return out


def _lineage_leaf_paths() -> list[tuple[str, ...]]:
    return (
        [(k,) for k in LINEAGE_ROW_KEYS]
        + [("lineage", k) for k in LINEAGE_LIST_FIELDS + LINEAGE_SCALAR_FIELDS]
        + [("metadata", k) for k in LINEAGE_META_FIELDS]
    )


def lineage_select_sql() -> str:
    """One jsonb leaf (as text) per lineage-row field; missing leaf = SQL NULL, JSON null = ``null``."""
    return ", ".join(f"({_sql_node(p)})::text" for p in _lineage_leaf_paths())


def lineage_rows_from_columns(rows) -> list[dict]:
    """Assemble ``storage.lineage_row``-shaped dicts from the select columns."""
    names = list(LINEAGE_ROW_KEYS) + list(LINEAGE_LIST_FIELDS + LINEAGE_SCALAR_FIELDS) + list(LINEAGE_META_FIELDS)
    return [
        {name: (json.loads(col) if col is not None else None) for name, col in zip(names, r)}
        for r in rows
    ]


def _strategy_artifact_leaf_paths() -> list[tuple[str, ...]]:
    return (
        [(k,) for k in STRATEGY_ARTIFACT_ROW_KEYS]
        + [("evaluation_summary", k) for k in STRATEGY_ARTIFACT_EVAL_KEYS]
    )


def strategy_artifact_select_sql() -> str:
    """One jsonb leaf (as text) per execution-bundle catalog field; no metadata decoded."""
    return ", ".join(f"({_sql_node(p)})::text" for p in _strategy_artifact_leaf_paths())


def strategy_artifact_rows_from_columns(rows) -> list[dict]:
    """Assemble ``storage.strategy_artifact_row``-shaped dicts from the select columns."""
    names = list(STRATEGY_ARTIFACT_ROW_KEYS) + list(STRATEGY_ARTIFACT_EVAL_KEYS)
    return [
        {name: (json.loads(col) if col is not None else None) for name, col in zip(names, r)}
        for r in rows
    ]


_STRATEGY_SUMMARY_CONTAINER_PATHS: tuple[tuple[str, ...], ...] = (
    ("metadata", "provenance"),
    ("metadata", "governance"),
    ("metadata", "market_scope"),
    ("metadata", "execution_profile"),
    ("metadata", "strategy_spec"),
    ("metadata", "strategy_spec", "provenance"),
    ("metadata", "strategy_spec", "governance"),
    ("metadata", "strategy_spec", "market_scope"),
    ("metadata", "strategy_spec", "execution_profile"),
    ("metadata", "strategy_spec", "metadata"),
)


def _strategy_summary_leaf_paths() -> list[tuple[str, ...]]:
    """Leaf paths read for a strategy-spec summary row, in assembly order."""
    meta = ("metadata",)
    spec = ("metadata", "strategy_spec")
    spec_meta = spec + ("metadata",)
    return (
        [(k,) for k in STRATEGY_SUMMARY_ROW_KEYS]
        + [meta + (k,) for k in STRATEGY_SUMMARY_META_KEYS]
        + [meta + ("provenance", k) for k in STRATEGY_SUMMARY_PROV_KEYS]
        + [meta + ("governance", k) for k in STRATEGY_SUMMARY_GOV_KEYS]
        + [meta + ("market_scope", k) for k in STRATEGY_SUMMARY_MARKET_KEYS]
        + [meta + ("execution_profile", k) for k in STRATEGY_SUMMARY_EXEC_KEYS]
        + [spec + (k,) for k in STRATEGY_SUMMARY_SPEC_KEYS]
        + [spec + ("provenance", k) for k in STRATEGY_SUMMARY_PROV_KEYS]
        + [spec + ("governance", k) for k in STRATEGY_SUMMARY_GOV_KEYS]
        + [spec + ("market_scope", k) for k in STRATEGY_SUMMARY_MARKET_KEYS]
        + [spec + ("execution_profile", k) for k in STRATEGY_SUMMARY_EXEC_KEYS]
        + [spec_meta + (k,) for k in STRATEGY_SUMMARY_INNER_META_KEYS]
        + [("evaluation_summary", k) for k in STRATEGY_ARTIFACT_EVAL_KEYS]
    )


def strategy_spec_summary_select_sql() -> str:
    """Plain column list over ``payload``: one jsonb leaf (as text) per summary field."""
    cols = ", ".join(f"({_sql_node(p)})::text" for p in _strategy_summary_leaf_paths())
    shapes = ", ".join(
        f"jsonb_typeof({_sql_node(p)})"
        for p in _STRATEGY_SUMMARY_CONTAINER_PATHS
    )
    return f"{cols}, {shapes}"


def strategy_spec_summary_rows_from_columns(rows) -> list[dict]:
    """Assemble ``strategy_spec_summary_row``-shaped dicts from the select columns."""
    n = len(_strategy_summary_leaf_paths())
    i_meta = len(STRATEGY_SUMMARY_ROW_KEYS)
    i_meta_prov = i_meta + len(STRATEGY_SUMMARY_META_KEYS)
    i_meta_gov = i_meta_prov + len(STRATEGY_SUMMARY_PROV_KEYS)
    i_meta_mkt = i_meta_gov + len(STRATEGY_SUMMARY_GOV_KEYS)
    i_meta_exec = i_meta_mkt + len(STRATEGY_SUMMARY_MARKET_KEYS)
    i_spec = i_meta_exec + len(STRATEGY_SUMMARY_EXEC_KEYS)
    i_spec_prov = i_spec + len(STRATEGY_SUMMARY_SPEC_KEYS)
    i_spec_gov = i_spec_prov + len(STRATEGY_SUMMARY_PROV_KEYS)
    i_spec_mkt = i_spec_gov + len(STRATEGY_SUMMARY_GOV_KEYS)
    i_spec_exec = i_spec_mkt + len(STRATEGY_SUMMARY_MARKET_KEYS)
    i_inner = i_spec_exec + len(STRATEGY_SUMMARY_EXEC_KEYS)
    i_eval = i_inner + len(STRATEGY_SUMMARY_INNER_META_KEYS)
    out: list[dict] = []
    for r in rows:
        def val(i):
            return json.loads(r[i])

        def pick(keys, start):
            return {k: val(start + j) for j, k in enumerate(keys) if r[start + j] is not None}

        row = pick(STRATEGY_SUMMARY_ROW_KEYS, 0)
        meta = pick(STRATEGY_SUMMARY_META_KEYS, i_meta)
        if r[n + 0] == "object":
            meta["provenance"] = pick(STRATEGY_SUMMARY_PROV_KEYS, i_meta_prov)
        if r[n + 1] == "object":
            meta["governance"] = pick(STRATEGY_SUMMARY_GOV_KEYS, i_meta_gov)
        if r[n + 2] == "object":
            meta["market_scope"] = pick(STRATEGY_SUMMARY_MARKET_KEYS, i_meta_mkt)
        if r[n + 3] == "object":
            meta["execution_profile"] = pick(STRATEGY_SUMMARY_EXEC_KEYS, i_meta_exec)
        if r[n + 4] == "object":
            spec = pick(STRATEGY_SUMMARY_SPEC_KEYS, i_spec)
            if r[n + 5] == "object":
                spec["provenance"] = pick(STRATEGY_SUMMARY_PROV_KEYS, i_spec_prov)
            if r[n + 6] == "object":
                spec["governance"] = pick(STRATEGY_SUMMARY_GOV_KEYS, i_spec_gov)
            if r[n + 7] == "object":
                spec["market_scope"] = pick(STRATEGY_SUMMARY_MARKET_KEYS, i_spec_mkt)
            if r[n + 8] == "object":
                spec["execution_profile"] = pick(STRATEGY_SUMMARY_EXEC_KEYS, i_spec_exec)
            if r[n + 9] == "object":
                spec["metadata"] = pick(STRATEGY_SUMMARY_INNER_META_KEYS, i_inner)
            meta["strategy_spec"] = spec
        eval_summary = pick(STRATEGY_ARTIFACT_EVAL_KEYS, i_eval)
        if eval_summary:
            row["evaluation_summary"] = eval_summary
        row["metadata"] = meta
        out.append(row)
    return out


_STRATEGY_SUMMARY_INDEX_CONTAINER_PATHS: tuple[tuple[str, ...], ...] = (
    ("metadata", "provenance"),
    ("metadata", "strategy_spec"),
    ("metadata", "strategy_spec", "provenance"),
    ("metadata", "strategy_spec", "metadata"),
)


def _strategy_summary_index_leaf_paths(*, include_title: bool = False) -> list[tuple[str, ...]]:
    """Leaf paths read for a strategy-spec candidate-index row (Phase 1)."""
    meta = ("metadata",)
    spec = ("metadata", "strategy_spec")
    spec_meta = spec + ("metadata",)
    paths = (
        [(k,) for k in STRATEGY_SUMMARY_INDEX_ROW_KEYS]
        + [meta + (k,) for k in STRATEGY_SUMMARY_INDEX_META_KEYS]
        + [meta + ("provenance", k) for k in STRATEGY_SUMMARY_INDEX_PROV_KEYS]
        + [spec + (k,) for k in STRATEGY_SUMMARY_INDEX_SPEC_KEYS]
        + [spec + ("provenance", k) for k in STRATEGY_SUMMARY_INDEX_PROV_KEYS]
        + [spec_meta + (k,) for k in STRATEGY_SUMMARY_INDEX_INNER_META_KEYS]
    )
    if include_title:
        paths = (
            paths
            + [meta + (k,) for k in STRATEGY_SUMMARY_INDEX_TITLE_KEYS]
            + [spec + (k,) for k in STRATEGY_SUMMARY_INDEX_TITLE_KEYS]
        )
    return paths


def _strategy_summary_index_scope_paths() -> list[tuple[str, ...]]:
    """``data_scope`` leaves read for the owner-side hide rule (metadata, then spec metadata)."""
    meta = ("metadata",)
    spec_meta = ("metadata", "strategy_spec", "metadata")
    return [meta + (k,) for k in STRATEGY_SUMMARY_INDEX_SCOPE_KEYS] + [
        spec_meta + (k,) for k in STRATEGY_SUMMARY_INDEX_SCOPE_KEYS
    ]


def strategy_spec_summary_index_select_sql(
    *, include_title: bool = False, include_scope: bool = False,
) -> str:
    """Plain column list over ``payload`` for candidate-index rows (Phase 1)."""
    cols = ", ".join(
        f"({_sql_node(p)})::text" for p in _strategy_summary_index_leaf_paths(include_title=include_title)
    )
    shapes = ", ".join(
        f"jsonb_typeof({_sql_node(p)})"
        for p in _STRATEGY_SUMMARY_INDEX_CONTAINER_PATHS
    )
    sql = f"{cols}, {shapes}"
    if include_scope:
        sql += ", " + ", ".join(
            f"({_sql_node(p)})::text" for p in _strategy_summary_index_scope_paths()
        )
    return sql


def _strategy_spec_summary_index_single_pass_sql(
    table: str, visible_sql: str, *, include_title: bool = False, include_scope: bool = False,
) -> str:
    """Single-pass Postgres query that detoasts ``payload`` once, extracts
    ``metadata`` and ``strategy_spec`` sub-objects once per visible row, and
    projects only the candidate-index fields before sorting."""
    title_cols_expr = (
        [f"(m -> '{k}')::text AS mt{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_TITLE_KEYS)]
        + [f"(s -> '{k}')::text AS st{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_TITLE_KEYS)]
        if include_title
        else []
    )
    title_outer_cols = (
        [f"mt{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_TITLE_KEYS))]
        + [f"st{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_TITLE_KEYS))]
        if include_title
        else []
    )
    cols_expr = (
        [f"(payload -> '{k}')::text AS c{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_ROW_KEYS)]
        + [f"(m -> '{k}')::text AS m{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_META_KEYS)]
        + [f"(mp -> '{k}')::text AS mp{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_PROV_KEYS)]
        + [f"(s -> '{k}')::text AS s{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_SPEC_KEYS)]
        + [f"(sp -> '{k}')::text AS sp{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_PROV_KEYS)]
        + [f"(sm -> '{k}')::text AS sm{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_INNER_META_KEYS)]
        + title_cols_expr
        + [
            "jsonb_typeof(mp) AS t0",
            "jsonb_typeof(s) AS t1",
            "jsonb_typeof(sp) AS t2",
            "jsonb_typeof(sm) AS t3",
        ]
    )
    outer_cols = (
        [f"c{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_ROW_KEYS))]
        + [f"m{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_META_KEYS))]
        + [f"mp{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_PROV_KEYS))]
        + [f"s{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_SPEC_KEYS))]
        + [f"sp{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_PROV_KEYS))]
        + [f"sm{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_INNER_META_KEYS))]
        + title_outer_cols
        + ["t0", "t1", "t2", "t3"]
    )
    if include_scope:
        scope_cols = (
            [f"(m -> '{k}')::text AS ds{i}" for i, k in enumerate(STRATEGY_SUMMARY_INDEX_SCOPE_KEYS)]
            + [
                f"(sm -> '{k}')::text AS dsm{i}"
                for i, k in enumerate(STRATEGY_SUMMARY_INDEX_SCOPE_KEYS)
            ]
        )
        cols_expr = cols_expr + scope_cols
        outer_cols = outer_cols + [
            f"ds{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_SCOPE_KEYS))
        ] + [f"dsm{i}" for i in range(len(STRATEGY_SUMMARY_INDEX_SCOPE_KEYS))]
    return (
        f"SELECT {', '.join(outer_cols)} FROM ("
        f"SELECT updated_at, {', '.join(cols_expr)} FROM ("
        "SELECT updated_at, payload, m, m -> 'provenance' AS mp, s, "
        "s -> 'provenance' AS sp, s -> 'metadata' AS sm FROM ("
        "SELECT updated_at, payload, payload -> 'metadata' AS m, "
        "(payload -> 'metadata') -> 'strategy_spec' AS s FROM ("
        "SELECT updated_at, CASE WHEN jsonb_typeof(u) = 'array' THEN "
        "(CASE WHEN jsonb_typeof(payload) = 'string' "
        "THEN (payload #>> '{}')::jsonb ELSE u END) ELSE u END AS payload "
        f"FROM (SELECT updated_at, payload, payload || '{{}}'::jsonb AS u "
        # Artifact type and tenant are decided on the stored payload before
        # ``payload || '{}'`` is built, so rows of other artifact types or
        # tenants are never copied. A non-object payload (legacy string or
        # array encoding) cannot be judged yet and is normalised first.
        f"FROM {table} WHERE jsonb_typeof(payload) <> 'object' OR ({visible_sql}) "
        "OFFSET 0) AS raw OFFSET 0"
        f") AS r1 WHERE jsonb_typeof(payload) = 'object' AND ({visible_sql}) OFFSET 0"
        ") AS r2 OFFSET 0"
        ") AS r3 OFFSET 0"
        ") AS r4 ORDER BY updated_at ASC"
    )


def strategy_spec_summary_index_rows_from_columns(
    rows, *, include_title: bool = False, include_scope: bool = False,
) -> list[dict]:
    """Assemble ``strategy_spec_summary_index_row``-shaped dicts from the index select columns."""
    loads = json.loads

    def _dec(raw: str):
        if raw == "null":
            return None
        if raw == "[]":
            return []
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            return raw[1:-1] if "\\" not in raw else loads(raw)
        return loads(raw)

    out: list[dict] = []
    append = out.append
    for raw_tuple in rows:
        if include_scope:
            # Scope leaves trail the fixed columns: data_scope / dataScope from
            # metadata, then from metadata.strategy_spec.metadata.
            raw_tuple, scope_cols = tuple(raw_tuple[:-4]), tuple(raw_tuple[-4:])
        else:
            scope_cols = ()
        if include_title:
            (
                c0, c1, c2, c3, c4, c5, c6,
                m0, m1, m2, m3, m4, m5, m6, m7, m8,
                mp0,
                s0, s1, s2, s3, s4, s5, s6, s7, s8,
                sp0,
                sm0, sm1, sm2, sm3,
                mt0, mt1, st0, st1,
                t0, t1, t2, t3,
            ) = raw_tuple
        else:
            (
                c0, c1, c2, c3, c4, c5, c6,
                m0, m1, m2, m3, m4, m5, m6, m7, m8,
                mp0,
                s0, s1, s2, s3, s4, s5, s6, s7, s8,
                sp0,
                sm0, sm1, sm2, sm3,
                t0, t1, t2, t3,
            ) = raw_tuple
            mt0 = mt1 = st0 = st1 = None
        meta: dict[str, Any] = {}
        if m0 is not None:
            meta["spec_version_id"] = _dec(m0)
        if m1 is not None:
            meta["spec_version"] = _dec(m1)
        if m2 is not None:
            meta["version"] = _dec(m2)
        if m3 is not None:
            meta["personaIds"] = _dec(m3)
        if m4 is not None:
            meta["persona_ids"] = _dec(m4)
        if m5 is not None:
            meta["persona_id"] = _dec(m5)
        if m6 is not None:
            meta["lifecycle_state"] = _dec(m6)
        if m7 is not None:
            meta["state"] = _dec(m7)
        if m8 is not None:
            meta["status"] = _dec(m8)
        if include_title:
            if mt0 is not None:
                meta["title"] = _dec(mt0)
            if mt1 is not None:
                meta["name"] = _dec(mt1)
        if scope_cols:
            for key, raw in zip(STRATEGY_SUMMARY_INDEX_SCOPE_KEYS, scope_cols[:2]):
                if raw is not None:
                    meta[key] = _dec(raw)
        if t0 == "object":
            meta["provenance"] = {"created_at": _dec(mp0)} if mp0 is not None else {}
        if t1 == "object":
            spec: dict[str, Any] = {}
            if s0 is not None:
                spec["strategy_id"] = _dec(s0)
            if s1 is not None:
                spec["spec_version"] = _dec(s1)
            if s2 is not None:
                spec["lifecycle_state"] = _dec(s2)
            if s3 is not None:
                spec["state"] = _dec(s3)
            if s4 is not None:
                spec["status"] = _dec(s4)
            if s5 is not None:
                spec["persona_ids"] = _dec(s5)
            if s6 is not None:
                spec["created_at"] = _dec(s6)
            if s7 is not None:
                spec["updated_at"] = _dec(s7)
            if s8 is not None:
                spec["last_modified_at"] = _dec(s8)
            if include_title:
                if st0 is not None:
                    spec["title"] = _dec(st0)
                if st1 is not None:
                    spec["name"] = _dec(st1)
            if t2 == "object":
                spec["provenance"] = {"created_at": _dec(sp0)} if sp0 is not None else {}
            if t3 == "object":
                inner_meta: dict[str, Any] = {}
                if sm0 is not None:
                    inner_meta["persona_id"] = _dec(sm0)
                if sm1 is not None:
                    inner_meta["lifecycle_state"] = _dec(sm1)
                if sm2 is not None:
                    inner_meta["state"] = _dec(sm2)
                if sm3 is not None:
                    inner_meta["status"] = _dec(sm3)
                if scope_cols:
                    for key, raw in zip(STRATEGY_SUMMARY_INDEX_SCOPE_KEYS, scope_cols[2:]):
                        if raw is not None:
                            inner_meta[key] = _dec(raw)
                spec["metadata"] = inner_meta
            meta["strategy_spec"] = spec
        row: dict[str, Any] = {"metadata": meta}
        if c0 is not None:
            row["registry_id"] = _dec(c0)
        if c1 is not None:
            row["artifact_type"] = _dec(c1)
        if c2 is not None:
            row["artifact_state"] = _dec(c2)
        if c3 is not None:
            row["strategy_id"] = _dec(c3)
        if c4 is not None:
            row["version"] = _dec(c4)
        if c5 is not None:
            row["created_at"] = _dec(c5)
        if c6 is not None:
            row["updated_at"] = _dec(c6)
        append(row)
    return out


_REGIME_STRATEGY_CONTAINER_PATHS: tuple[tuple[str, ...], ...] = (
    ("metadata", "strategy_spec"),
    ("metadata", "strategy_spec", "metadata"),
)


def _regime_strategy_spec_leaf_paths() -> list[tuple[str, ...]]:
    """Leaf paths read for a StrategySpec regime-evaluation row."""
    meta = ("metadata",)
    spec = ("metadata", "strategy_spec")
    spec_meta = spec + ("metadata",)
    return (
        [(k,) for k in REGIME_EVAL_STRATEGY_ROW_KEYS]
        + [meta + (k,) for k in REGIME_EVAL_STRATEGY_META_KEYS]
        + [spec + (k,) for k in REGIME_EVAL_STRATEGY_SPEC_KEYS]
        + [spec_meta + (k,) for k in REGIME_EVAL_STRATEGY_INNER_META_KEYS]
    )


def regime_strategy_spec_select_sql() -> str:
    """Plain column list over ``payload`` for StrategySpec regime-evaluation rows."""
    cols = ", ".join(f"({_sql_node(p)})::text" for p in _regime_strategy_spec_leaf_paths())
    shapes = ", ".join(f"jsonb_typeof({_sql_node(p)})" for p in _REGIME_STRATEGY_CONTAINER_PATHS)
    return f"{cols}, {shapes}"


def _regime_strategy_spec_single_pass_sql(table: str, pred_sql: str) -> str:
    """Single-pass Postgres query that filters ``strategy_spec``, non-retired
    ``artifact_state``, and ``visible_tenants`` in SQL (unwrapping legacy
    JSON-string payloads), detoasts ``payload`` once per kept row, and projects
    only the regime-evaluation fields."""
    cols_expr = (
        [f"(payload -> '{k}')::text AS c{i}" for i, k in enumerate(REGIME_EVAL_STRATEGY_ROW_KEYS)]
        + [f"(m -> '{k}')::text AS m{i}" for i, k in enumerate(REGIME_EVAL_STRATEGY_META_KEYS)]
        + [f"(s -> '{k}')::text AS s{i}" for i, k in enumerate(REGIME_EVAL_STRATEGY_SPEC_KEYS)]
        + [f"(sm -> '{k}')::text AS sm{i}" for i, k in enumerate(REGIME_EVAL_STRATEGY_INNER_META_KEYS)]
        + [
            "jsonb_typeof(s) AS t0",
            "jsonb_typeof(sm) AS t1",
        ]
    )
    outer_cols = (
        [f"c{i}" for i in range(len(REGIME_EVAL_STRATEGY_ROW_KEYS))]
        + [f"m{i}" for i in range(len(REGIME_EVAL_STRATEGY_META_KEYS))]
        + [f"s{i}" for i in range(len(REGIME_EVAL_STRATEGY_SPEC_KEYS))]
        + [f"sm{i}" for i in range(len(REGIME_EVAL_STRATEGY_INNER_META_KEYS))]
        + ["t0", "t1"]
    )
    return (
        f"SELECT {', '.join(outer_cols)} FROM ("
        f"SELECT updated_at, {', '.join(cols_expr)} FROM ("
        "SELECT updated_at, payload, m, s, s -> 'metadata' AS sm FROM ("
        "SELECT updated_at, payload, payload -> 'metadata' AS m, "
        "(payload -> 'metadata') -> 'strategy_spec' AS s FROM ("
        "SELECT updated_at, CASE WHEN jsonb_typeof(u) = 'array' THEN "
        "(CASE WHEN jsonb_typeof(payload) = 'string' "
        "THEN (payload #>> '{}')::jsonb ELSE u END) ELSE u END AS payload "
        f"FROM (SELECT updated_at, payload, payload || '{{}}'::jsonb AS u "
        f"FROM {table} WHERE jsonb_typeof(payload) <> 'object' OR ({pred_sql}) "
        "OFFSET 0) AS raw OFFSET 0"
        f") AS r1 WHERE jsonb_typeof(payload) = 'object' AND ({pred_sql}) OFFSET 0"
        ") AS r2 OFFSET 0"
        ") AS r3 OFFSET 0"
        ") AS r4 ORDER BY updated_at ASC"
    )


def regime_strategy_spec_rows_from_columns(rows) -> list[dict]:
    """Assemble ``regime_evaluate_strategy_row``-shaped dicts from the select columns."""
    loads = json.loads

    def _dec(raw: str):
        if raw == "null":
            return None
        if raw == "[]":
            return []
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            return raw[1:-1] if "\\" not in raw else loads(raw)
        return loads(raw)

    out: list[dict] = []
    append = out.append
    for (
        c0, c1, c2, c3, c4,
        m0, m1, m2, m3, m4,
        s0, s1, s2, s3,
        sm0,
        t0, t1,
    ) in rows:
        meta: dict[str, Any] = {}
        if m0 is not None:
            meta["regime"] = _dec(m0)
        if m1 is not None:
            meta["holding_horizon_sessions"] = _dec(m1)
        if m2 is not None:
            meta["family"] = _dec(m2)
        if m3 is not None:
            meta["strategy_family"] = _dec(m3)
        if m4 is not None:
            meta["applicable_regimes"] = _dec(m4)
        if t0 == "object":
            spec: dict[str, Any] = {}
            if s0 is not None:
                spec["regime"] = _dec(s0)
            if s1 is not None:
                spec["holding_horizon_sessions"] = _dec(s1)
            if s2 is not None:
                spec["family"] = _dec(s2)
            if s3 is not None:
                spec["applicable_regimes"] = _dec(s3)
            if t1 == "object" and sm0 is not None:
                spec["metadata"] = {"regime": _dec(sm0)}
            if spec:
                meta["strategy_spec"] = spec
        append({
            "registry_id": _dec(c0) if c0 is not None else None,
            "strategy_id": _dec(c1) if c1 is not None else None,
            "version": _dec(c2) if c2 is not None else None,
            "artifact_state": _dec(c3) if c3 is not None else None,
            "owner_tenant": _dec(c4) if c4 is not None else None,
            "metadata": meta,
        })
    return out


def _regime_model_artifact_leaf_paths() -> list[tuple[str, ...]]:
    """Leaf paths read for a ModelArtifact regime-evaluation row."""
    meta = ("metadata",)
    return (
        [(k,) for k in REGIME_EVAL_MODEL_ROW_KEYS]
        + [meta + (k,) for k in REGIME_EVAL_MODEL_META_KEYS]
    )


def regime_model_artifact_select_sql() -> str:
    """Plain column list over ``payload`` for ModelArtifact regime-evaluation rows."""
    cols = ", ".join(f"({_sql_node(p)})::text" for p in _regime_model_artifact_leaf_paths())
    return f"{cols}, jsonb_typeof({_sql_node(('metadata',))})"


def _regime_model_artifact_single_pass_sql(table: str, pred_sql: str) -> str:
    """Single-pass Postgres query for ModelArtifact regime-evaluation rows."""
    cols_expr = (
        [f"(payload -> '{k}')::text AS c{i}" for i, k in enumerate(REGIME_EVAL_MODEL_ROW_KEYS)]
        + [f"(m -> '{k}')::text AS m{i}" for i, k in enumerate(REGIME_EVAL_MODEL_META_KEYS)]
        + ["jsonb_typeof(m) AS t0"]
    )
    outer_cols = (
        [f"c{i}" for i in range(len(REGIME_EVAL_MODEL_ROW_KEYS))]
        + [f"m{i}" for i in range(len(REGIME_EVAL_MODEL_META_KEYS))]
        + ["t0"]
    )
    return (
        f"SELECT {', '.join(outer_cols)} FROM ("
        f"SELECT updated_at, {', '.join(cols_expr)} FROM ("
        "SELECT updated_at, payload, payload -> 'metadata' AS m FROM ("
        "SELECT updated_at, CASE WHEN jsonb_typeof(u) = 'array' THEN "
        "(CASE WHEN jsonb_typeof(payload) = 'string' "
        "THEN (payload #>> '{}')::jsonb ELSE u END) ELSE u END AS payload "
        f"FROM (SELECT updated_at, payload, payload || '{{}}'::jsonb AS u "
        f"FROM {table} WHERE jsonb_typeof(payload) <> 'object' OR ({pred_sql}) "
        "OFFSET 0) AS raw OFFSET 0"
        f") AS r1 WHERE jsonb_typeof(payload) = 'object' AND ({pred_sql}) OFFSET 0"
        ") AS r2 OFFSET 0"
        ") AS r3 ORDER BY updated_at ASC"
    )


def regime_model_artifact_rows_from_columns(rows) -> list[dict]:
    """Assemble ``regime_evaluate_model_row``-shaped dicts from the select columns."""
    loads = json.loads

    def _dec(raw: str):
        if raw == "null":
            return None
        if raw == "[]":
            return []
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            return raw[1:-1] if "\\" not in raw else loads(raw)
        return loads(raw)

    out: list[dict] = []
    append = out.append
    for (
        c0, c1, c2,
        m0, m1, m2, m3, m4, m5, m6, m7, m8,
        t0,
    ) in rows:
        meta: dict[str, Any] = {}
        if t0 == "object":
            for key, raw in zip(
                REGIME_EVAL_MODEL_META_KEYS,
                (m0, m1, m2, m3, m4, m5, m6, m7, m8),
            ):
                if raw is not None:
                    meta[key] = _dec(raw)
        append({
            "registry_id": _dec(c0) if c0 is not None else None,
            "artifact_state": _dec(c1) if c1 is not None else None,
            "created_at": _dec(c2) if c2 is not None else None,
            "metadata": meta,
        })
    return out


def _registry_backend() -> str:
    return os.getenv("REGISTRY_STORE_BACKEND", "memory").strip().lower()


def _registry_dsn() -> str:
    dsn = os.getenv("REGISTRY_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("REGISTRY_STORE_DSN or DATABASE_URL is required for Postgres registry store")
    return dsn


def _registry_bootstrap() -> bool:
    return os.getenv("REGISTRY_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")


def build_postgres_registry_store() -> PostgresRegistryStore:
    """Construct the production Postgres registry store from environment config.

    Raises with an explicit configuration error rather than silently falling
    back to an in-memory store when Postgres is selected but unreachable/
    unconfigured — memory is a test double injected directly by tests, never
    a missing-config fallback for the selected production backend.
    """
    return PostgresRegistryStore(
        dsn=_registry_dsn(),
        entries_table=os.getenv("REGISTRY_ENTRIES_TABLE", "registry.entries"),
        receipts_table=os.getenv("REGISTRY_RECEIPTS_TABLE", "registry.command_receipts"),
        bootstrap=_registry_bootstrap(),
    )
