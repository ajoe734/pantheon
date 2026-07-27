"""Backend-independent reader for tenant-scoped Agora DatasetVersions.

L12-IMIT-001.  Before this module the scheduler only discovered datasets when
``POLICY_LEARNING_STORE_BACKEND=postgres`` — the policy-learning *candidate*
store backend gated whether real Agora data could be seen at all.  In default
compose that backend is ``json``, so the product path silently discovered
nothing and every candidate trained on the seed fixture.

The Agora dataset authority is a separate owner (L12-AGORA-001) with its own
connection settings.  This reader resolves it from ``AGORA_DATASET_STORE_*``
(falling back to the shared ``DATABASE_URL``) so discovery works the same
whether policy-learning persists candidates in JSON or Postgres.

Fail-closed contract: when the authority cannot be resolved or reached, this
module raises :class:`AgoraAuthorityUnavailable`.  It has no seed, fixture, or
synthesized fallback path.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Mapping, Optional, Sequence

from services.research.imitation.agora_dataset_source import (
    AgoraDatasetVersion,
    LEARN_DATASET_KIND,
)


BACKEND_ENV = "AGORA_DATASET_STORE_BACKEND"
DSN_ENV = "AGORA_DATASET_STORE_DSN"
SCHEMA_ENV = "AGORA_DATASET_STORE_SCHEMA"
TENANT_ENV = "POLICY_LEARNING_AGORA_TENANT_ID"
USER_ENV = "POLICY_LEARNING_AGORA_USER_ID"
DEFAULT_SCHEMA = "agora"
RECORDS_TABLE = "agora_dataset_records"

_MEMORY_BACKENDS = {"", "off", "false", "none", "memory", ":memory:"}

_logger = logging.getLogger("policy-learning.agora-authority")


def _dsn_has_credentials(dsn: str) -> bool:
    """True when the connection will present an identity to Postgres.

    ``agora.agora_dataset_records`` is owned by the Agora dataset-extraction
    service.  A cross-service read of it must go through an authenticated read
    role (DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY §3.2); an anonymous or
    ``trust``-authenticated connection is not one, and reading another owner's
    tenant-partitioned rows without an identity is exactly the failure this
    task has to close.
    """

    if not dsn:
        return False
    for name in ("PGUSER", "PGPASSFILE", "PGSERVICE"):
        if (os.getenv(name) or "").strip():
            return True
    try:
        from psycopg.conninfo import conninfo_to_dict  # type: ignore[import]
    except ImportError:  # pragma: no cover - driver absent is handled elsewhere
        return "@" in dsn or "user=" in dsn
    try:
        parsed = conninfo_to_dict(dsn)
    except Exception:
        return False
    return bool(str(parsed.get("user") or "").strip())


class AgoraAuthorityUnavailable(RuntimeError):
    """The Agora dataset authority could not be resolved or queried.

    ``reason`` is a stable machine token recorded in degradation evidence so an
    operator can tell "not configured" apart from "configured but unreachable".
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": self.detail}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def configured_tenant_id() -> str:
    """Tenant scope the scheduler discovers under."""

    return _env(TENANT_ENV)


def configured_user_id() -> str:
    """Optional user scope; empty means every user inside the tenant."""

    return _env(USER_ENV)


def _quote(identifier: str) -> str:
    if not identifier or any(character in identifier for character in '"\\\x00'):
        raise AgoraAuthorityUnavailable(
            "agora_authority_misconfigured",
            f"Invalid Agora schema identifier: {identifier!r}",
        )
    return f'"{identifier}"'


class AgoraDatasetAuthority:
    """Read-only, tenant-scoped view of ``agora.agora_dataset_records``.

    The ``memory`` backend exists so tests and offline compose runs can inject
    an explicit record set.  An empty memory authority is *unavailable*, not an
    empty success: silently returning zero datasets would be indistinguishable
    from a healthy tenant with no data, and the caller must be able to tell.
    """

    def __init__(
        self,
        *,
        backend: Optional[str] = None,
        dsn: Optional[str] = None,
        schema: Optional[str] = None,
        records: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        self._injected_records = list(records) if records is not None else None
        requested = (backend if backend is not None else _env(BACKEND_ENV)).strip().lower()
        self._read_role_dsn = _env(DSN_ENV) if dsn is None else _clean(dsn)
        self.dsn = self._read_role_dsn or (_env("DATABASE_URL") if dsn is None else "")
        self.schema = (schema if schema is not None else _env(SCHEMA_ENV)) or DEFAULT_SCHEMA

        self._unsupported = ""
        if requested == "postgres":
            self.backend = "postgres"
        elif self._injected_records is not None and requested in _MEMORY_BACKENDS:
            self.backend = "memory"
        elif not requested:
            # Unset: the product default follows the shared DSN.  Compose hands
            # policy-learning DATABASE_URL, so the default path is real data.
            self.backend = "postgres" if self.dsn else "unconfigured"
        elif requested in _MEMORY_BACKENDS:
            self.backend = "memory"
        else:
            self.backend = "unsupported"
            self._unsupported = requested

        if self.backend == "postgres" and not self.dsn:
            self.backend = "unconfigured"

    def describe(self) -> dict[str, Any]:
        """Non-secret description of how the authority was resolved."""

        return {
            "authority": "agora.dataset.v1",
            "backend": self.backend,
            "schema": self.schema,
            "dsn_configured": bool(self.dsn),
            "dedicated_read_role_dsn": bool(self._read_role_dsn),
            "authenticated": self.backend != "postgres" or _dsn_has_credentials(self.dsn),
            "session_access": "read_only",
            "records_table": f"{self.schema}.{RECORDS_TABLE}",
            "independent_of_policy_store_backend": True,
            "seed_fallback": "none",
        }

    # -- resolution -----------------------------------------------------

    def _require_backend(self) -> None:
        if self.backend == "unconfigured":
            raise AgoraAuthorityUnavailable(
                "agora_authority_unconfigured",
                f"Set {BACKEND_ENV}/{DSN_ENV} (or DATABASE_URL) so policy-learning can read "
                "tenant-scoped Agora DatasetVersions",
            )
        if self.backend == "unsupported":
            raise AgoraAuthorityUnavailable(
                "agora_authority_misconfigured",
                f"Unsupported {BACKEND_ENV}={self._unsupported!r}; "
                "expected postgres or memory",
            )

    @staticmethod
    def _require_tenant(tenant_id: str) -> str:
        resolved = (tenant_id or "").strip()
        if not resolved:
            raise AgoraAuthorityUnavailable(
                "tenant_scope_unset",
                f"A tenant scope is required for Agora dataset discovery; set {TENANT_ENV} "
                "or pass tenant_id on the tick",
            )
        return resolved

    def _require_authenticated(self) -> None:
        if not _dsn_has_credentials(self.dsn):
            raise AgoraAuthorityUnavailable(
                "agora_authority_unauthenticated",
                f"{DSN_ENV} (or DATABASE_URL) must carry a database identity so the "
                "Agora dataset authority is read through an authenticated read role",
            )

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - exercised via env in compose
            raise AgoraAuthorityUnavailable(
                "agora_authority_driver_missing",
                "psycopg is required to read the Postgres Agora dataset authority",
            ) from exc
        self._require_authenticated()
        try:
            connection = psycopg.connect(self.dsn)
        except Exception as exc:
            raise AgoraAuthorityUnavailable(
                "agora_authority_unreachable",
                f"Could not connect to the Agora dataset authority: {exc}",
            ) from exc
        try:
            # policy-learning is a non-owner reader of agora.*.  A read-only
            # session makes that boundary enforced by Postgres rather than by
            # this module remembering to only write SELECTs.
            connection.read_only = True
        except Exception as exc:
            connection.close()
            raise AgoraAuthorityUnavailable(
                "agora_authority_read_only_unavailable",
                f"Could not pin the Agora dataset authority session read-only: {exc}",
            ) from exc
        return connection

    # -- queries --------------------------------------------------------

    _COLUMNS = (
        "evidence_id",
        "dataset_version_id",
        "dataset_kind",
        "interaction_kind",
        "persona_id",
        "session_id",
        "tenant_id",
        "user_id",
        "content",
        "source_refs",
        "learning_eligible",
        "captured_at",
        "extracted_at",
        "version",
    )

    def _memory_rows(self) -> list[Mapping[str, Any]]:
        return list(self._injected_records or [])

    def _select(
        self,
        *,
        tenant_id: str,
        user_id: str,
        dataset_kind: str,
        dataset_version_id: str = "",
        limit: Optional[int] = None,
    ) -> list[Mapping[str, Any]]:
        self._require_backend()
        tenant = self._require_tenant(tenant_id)

        if self.backend == "memory":
            rows = [
                row
                for row in self._memory_rows()
                if str(row.get("tenant_id") or "") == tenant
                and (not user_id or str(row.get("user_id") or "") == user_id)
                and (not dataset_kind or str(row.get("dataset_kind") or "") == dataset_kind)
                and (
                    not dataset_version_id
                    or str(row.get("dataset_version_id") or "") == dataset_version_id
                )
                and bool(row.get("learning_eligible", True))
            ]
            rows.sort(key=lambda row: (str(row.get("extracted_at") or ""), str(row.get("dataset_version_id") or "")))
            return rows[:limit] if limit else rows

        columns = ", ".join(self._COLUMNS)
        table = f"{_quote(self.schema)}.{_quote(RECORDS_TABLE)}"
        clauses = ["tenant_id = %s", "learning_eligible = true"]
        params: list[Any] = [tenant]
        if user_id:
            clauses.append("user_id = %s")
            params.append(user_id)
        if dataset_kind:
            clauses.append("dataset_kind = %s")
            params.append(dataset_kind)
        if dataset_version_id:
            clauses.append("dataset_version_id = %s")
            params.append(dataset_version_id)
        sql = (
            f"SELECT {columns} FROM {table} WHERE {' AND '.join(clauses)} "
            "ORDER BY extracted_at, dataset_version_id"
        )
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))

        try:
            with self._connect() as conn:
                rows = conn.execute(sql, tuple(params)).fetchall()
        except AgoraAuthorityUnavailable:
            raise
        except Exception as exc:
            raise AgoraAuthorityUnavailable(
                "agora_authority_query_failed",
                f"Agora dataset authority query failed: {exc}",
            ) from exc
        return [dict(zip(self._COLUMNS, row)) for row in rows]

    def list_dataset_versions(
        self,
        *,
        tenant_id: str,
        user_id: str = "",
        dataset_kind: str = LEARN_DATASET_KIND,
        limit: Optional[int] = None,
    ) -> list[AgoraDatasetVersion]:
        """List learning-eligible dataset versions inside one tenant scope."""

        rows = self._select(
            tenant_id=tenant_id,
            user_id=user_id,
            dataset_kind=dataset_kind,
            limit=limit,
        )
        versions: list[AgoraDatasetVersion] = []
        for row in rows:
            try:
                versions.append(AgoraDatasetVersion.from_record(row))
            except ValueError as exc:
                # A malformed row must not poison the whole tick; it is dropped
                # from discovery and recorded, never repaired with defaults.
                _logger.warning("Skipping malformed Agora dataset record: %s", exc)
        return versions

    def get_dataset_versions(
        self,
        dataset_version_id: str,
        *,
        tenant_id: str,
        user_id: str = "",
        dataset_kind: str = LEARN_DATASET_KIND,
    ) -> list[AgoraDatasetVersion]:
        """Fetch the versions backing one dataset reference, tenant-scoped.

        Raises:
            AgoraAuthorityUnavailable: the authority is unresolvable,
                unreachable, or holds no matching version in this tenant.
        """

        resolved = (dataset_version_id or "").strip()
        if not resolved:
            raise AgoraAuthorityUnavailable(
                "dataset_version_id_missing",
                "A dataset_version_id is required to resolve Agora dataset content",
            )
        rows = self._select(
            tenant_id=tenant_id,
            user_id=user_id,
            dataset_kind=dataset_kind,
            dataset_version_id=resolved,
        )
        versions = [AgoraDatasetVersion.from_record(row) for row in rows]
        if not versions:
            raise AgoraAuthorityUnavailable(
                "dataset_version_not_found",
                f"Agora DatasetVersion {resolved!r} is not visible in tenant "
                f"{tenant_id!r}",
            )
        return versions


def build_agora_dataset_authority(
    records: Optional[Iterable[Mapping[str, Any]]] = None,
) -> AgoraDatasetAuthority:
    """Build the process-wide authority from environment configuration."""

    return AgoraDatasetAuthority(records=list(records) if records is not None else None)


__all__ = [
    "AgoraAuthorityUnavailable",
    "AgoraDatasetAuthority",
    "BACKEND_ENV",
    "DSN_ENV",
    "SCHEMA_ENV",
    "TENANT_ENV",
    "USER_ENV",
    "build_agora_dataset_authority",
    "configured_tenant_id",
    "configured_user_id",
]
