"""Persona strategy pool record (``persona_strategy_pool.v1``).

Design: SD_PERSONA_STRATEGY_POOL_AUTONOMY_2026-11-12 §4.1.

One append-only, versioned record per persona.  Every change appends a new
version; earlier versions stay readable and removed members stay in history.
Removing a member never touches the persona lifecycle (this module has no
dependency on the persona lifecycle writer at all).

Execution does not read members from this store; it reads the envelope
artifact (reviewer decision R6).  This store is the persona's book of
membership.

Fail-closed rules:
  * a non-removed member must be an approved registry entry whose checksum
    matches the supplied checksum, otherwise the change is refused and the
    refusal reason is recorded;
  * an unreachable / unconfigured registry is an availability failure, not a
    verdict -- nothing is written;
  * the first member is derived only from owner records (registry entries
    bound to the persona); zero or several candidates is a refusal, never a
    guess; no persona or artifact id is hard-coded.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrllibRequest, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "persona_strategy_pool.v1"
STAGES = ("trial", "active", "removed")
_WEIGHT_EPSILON = 1e-9


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class PoolError(ValueError):
    status_code = 422


class PoolNotFound(PoolError):
    status_code = 404


class PoolVersionConflict(PoolError):
    status_code = 409


class PoolMemberRefused(PoolError):
    """A member change was refused; ``reason`` has been recorded."""

    def __init__(self, reason: str, *, artifact_ref: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.artifact_ref = artifact_ref


class RegistryUnavailable(PoolError):
    status_code = 503


class PoolMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_ref: str = Field(min_length=1)
    checksum: str = Field(min_length=1)
    stage: str
    risk_weight: float
    baseline_ref: str | None = None
    joined_at: str = Field(min_length=1)
    parent_ref: str | None = None
    demand_ref: str | None = None
    removed_at: str | None = None
    removal_reason: str | None = None

    @field_validator("stage")
    @classmethod
    def _stage(cls, value: str) -> str:
        if value not in STAGES:
            raise ValueError(f"stage must be one of {list(STAGES)}")
        return value

    @field_validator("risk_weight")
    @classmethod
    def _weight(cls, value: float) -> float:
        if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("risk_weight must be a finite number in [0, 1]")
        return float(value)

    @model_validator(mode="after")
    def _consistency(self) -> "PoolMember":
        if self.stage == "active" and not (self.baseline_ref or "").strip():
            raise ValueError("an active member requires baseline_ref")
        if self.stage == "removed" and not (self.removal_reason or "").strip():
            raise ValueError("a removed member requires removal_reason")
        return self


class AppendPoolVersionRequest(BaseModel):
    """The complete membership of the new version.

    ``expected_version`` is the version the caller read (0 when creating); a
    stale value is refused so concurrent writers cannot overwrite each other.
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    members: list[PoolMember]
    change_reason: str = Field(min_length=1)
    actor_id: str | None = None


class RegistryEntryReader(Protocol):
    def __call__(self, registry_id: str, tenant_id: str) -> Mapping[str, Any]: ...


class OwnerArtifactLister(Protocol):
    """Registry entries bound to a persona (owner records), approved only."""

    def __call__(self, persona_id: str, tenant_id: str) -> list[Mapping[str, Any]]: ...


def _http_json(path: str, tenant_id: str) -> Any:
    from services.service_token_file import configured_service_token

    url = (os.getenv("PERSONA_REGISTRY_BASE_URL") or "").rstrip("/")
    try:
        token = configured_service_token("PERSONA_REGISTRY_SERVICE_TOKEN")
    except RuntimeError as exc:
        raise RegistryUnavailable("registry read principal unavailable") from exc
    if not url or not token:
        raise RegistryUnavailable("registry owner URL and read principal are required")
    request = UrllibRequest(
        url + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "X-Tenant-Id": tenant_id},
        method="GET",
    )
    timeout = float(os.getenv("PERSONA_REGISTRY_TIMEOUT_SECONDS", "5"))
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-configured URL
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RegistryUnavailable(f"registry read failed: HTTP {exc.code}") from exc
    except (URLError, OSError, ValueError) as exc:
        raise RegistryUnavailable("registry read unavailable or malformed") from exc


def http_registry_entry_reader(registry_id: str, tenant_id: str) -> Mapping[str, Any]:
    payload = _http_json("/api/registry/entries/" + quote(registry_id, safe=""), tenant_id)
    if payload is None:
        return {}
    entry = payload.get("entry") if isinstance(payload, Mapping) else None
    if not isinstance(entry, Mapping) or entry.get("registry_id") != registry_id:
        raise RegistryUnavailable("registry returned a malformed or mismatched entry")
    return entry


def http_owner_artifact_lister(persona_id: str, tenant_id: str) -> list[Mapping[str, Any]]:
    """Approved paper execution artifacts the registry binds to this persona."""
    found: list[Mapping[str, Any]] = []
    offset = 0
    while True:
        payload = _http_json(
            f"/api/registry/strategy-artifacts?artifact_state=approved&limit=200&offset={offset}",
            tenant_id,
        )
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            raise RegistryUnavailable("registry catalog response is malformed")
        for row in items:
            registry_id = str((row or {}).get("registry_id") or "")
            if not registry_id:
                continue
            entry = http_registry_entry_reader(registry_id, tenant_id)
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), Mapping) else {}
            if (
                metadata.get("persona_id") == persona_id
                and metadata.get("execution_context") == "paper"
                and entry.get("owner_tenant") in (tenant_id, None)
            ):
                found.append(entry)
        nxt = payload.get("next_page_token")
        if not nxt:
            return found
        offset = int(nxt)


def _verify_member(
    member: PoolMember, tenant_id: str, reader: RegistryEntryReader
) -> None:
    entry = reader(member.artifact_ref, tenant_id)
    if not entry:
        raise PoolMemberRefused("artifact is not a registry entry", artifact_ref=member.artifact_ref)
    state = str(getattr(entry.get("artifact_state"), "value", entry.get("artifact_state")) or "")
    if state != "approved":
        raise PoolMemberRefused(
            f"registry entry is not approved (state={state or 'unknown'})", artifact_ref=member.artifact_ref
        )
    if str(entry.get("checksum") or "") != member.checksum:
        raise PoolMemberRefused("checksum does not match the registry entry", artifact_ref=member.artifact_ref)


class PersonaStrategyPoolStore:
    """Append-only pool records over a compare-and-set record store."""

    def __init__(
        self,
        records: Any,
        *,
        registry_reader: RegistryEntryReader | None = None,
        owner_lister: OwnerArtifactLister | None = None,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self._records = records
        self._reader = registry_reader or http_registry_entry_reader
        self._lister = owner_lister or http_owner_artifact_lister
        self._now = now

    # -- reads -------------------------------------------------------------
    def _record(self, persona_id: str, tenant_id: str) -> dict[str, Any]:
        record = self._records.get(persona_id)
        if not record or record.get("tenant_id") != tenant_id or not record.get("versions"):
            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
        return record

    def current(self, persona_id: str, tenant_id: str) -> dict[str, Any]:
        return dict(self._record(persona_id, tenant_id)["versions"][-1])

    def versions(self, persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        record = self._records.get(persona_id)
        if not record or record.get("tenant_id") != tenant_id:
            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
        return [dict(v) for v in record["versions"]]

    def version(self, persona_id: str, tenant_id: str, number: int) -> dict[str, Any]:
        for item in self._record(persona_id, tenant_id)["versions"]:
            if item["pool_version"] == number:
                return dict(item)
        raise PoolNotFound(f"pool version {number} not found")

    def refusals(self, persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        record = self._records.get(persona_id)
        if not record or record.get("tenant_id") != tenant_id:
            return []
        return [dict(r) for r in record.get("refusals", [])]

    # -- writes ------------------------------------------------------------
    def append(
        self,
        persona_id: str,
        tenant_id: str,
        request: AppendPoolVersionRequest,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        existing = self._records.get(persona_id)
        if existing is not None and existing.get("tenant_id") != tenant_id:
            # Never reveal or touch another tenant's record.
            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
        versions = list(existing["versions"]) if existing else []
        current_number = len(versions)
        if request.expected_version != current_number:
            raise PoolVersionConflict(
                f"expected_version {request.expected_version} does not match current {current_number}"
            )
        previous = versions[-1] if versions else None
        try:
            self._validate_membership(previous, request.members, tenant_id)
        except PoolMemberRefused as exc:
            self._record_refusal(persona_id, tenant_id, existing, request, actor_id, exc)
            raise
        body = {
            "schema_version": SCHEMA_VERSION,
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "pool_version": current_number + 1,
            "members": [m.model_dump() for m in request.members],
            "change_reason": request.change_reason,
            "created_at": self._now(),
            "created_by": actor_id,
            "previous_digest": previous["digest"] if previous else None,
        }
        body["digest"] = canonical_digest(body)
        record = {
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "versions": versions + [body],
            "refusals": list(existing.get("refusals", [])) if existing else [],
        }
        ok, _ = self._records.compare_and_set(persona_id, existing, record)
        if not ok:
            raise PoolVersionConflict("pool changed concurrently; re-read and retry")
        return dict(body)

    def _validate_membership(
        self, previous: Mapping[str, Any] | None, members: list[PoolMember], tenant_id: str
    ) -> None:
        refs = [m.artifact_ref for m in members]
        if len(set(refs)) != len(refs):
            raise PoolMemberRefused("duplicate artifact_ref in membership")
        prior = {m["artifact_ref"]: m for m in (previous or {}).get("members", [])}
        by_ref = {m.artifact_ref: m for m in members}
        for ref in prior:
            if ref not in by_ref:
                raise PoolMemberRefused(
                    "a member cannot be dropped from history; mark it removed", artifact_ref=ref
                )
        total = 0.0
        for member in members:
            before = prior.get(member.artifact_ref)
            if before is not None:
                if before["stage"] == "removed" and member.stage != "removed":
                    raise PoolMemberRefused(
                        "a removed member cannot be re-activated; add a new member",
                        artifact_ref=member.artifact_ref,
                    )
                if before["checksum"] != member.checksum or before["joined_at"] != member.joined_at:
                    raise PoolMemberRefused(
                        "a member's checksum and joined_at are immutable", artifact_ref=member.artifact_ref
                    )
            if member.stage == "removed":
                continue
            total += member.risk_weight
            unchanged = before is not None and before["stage"] == member.stage
            if not unchanged:
                _verify_member(member, tenant_id, self._reader)
        if total > 1.0 + _WEIGHT_EPSILON:
            raise PoolMemberRefused(f"risk weights of live members sum to {total:.6f}, above 1.0")

    def _record_refusal(
        self,
        persona_id: str,
        tenant_id: str,
        existing: Mapping[str, Any] | None,
        request: AppendPoolVersionRequest,
        actor_id: str,
        exc: PoolMemberRefused,
    ) -> None:
        if existing is None:
            # Nothing to attach a refusal to without creating a pool; keep the
            # record version-less so reads still report "no pool".
            record: dict[str, Any] = {"persona_id": persona_id, "tenant_id": tenant_id, "versions": [], "refusals": []}
        else:
            record = dict(existing)
        record["refusals"] = list(record.get("refusals", [])) + [
            {
                "refused_at": self._now(),
                "actor_id": actor_id,
                "artifact_ref": exc.artifact_ref,
                "reason": exc.reason,
                "attempted_against_version": len(existing["versions"]) if existing else 0,
                "change_reason": request.change_reason,
            }
        ]
        self._records.compare_and_set(persona_id, existing, record)

    # -- first member ------------------------------------------------------
    def seed_from_owner_records(
        self, persona_id: str, tenant_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        """Version 1: the persona's one approved paper artifact becomes member 1.

        Everything is read from registry owner records.  Zero or several
        candidates, or a missing baseline reference, fails closed.
        """
        existing = self._records.get(persona_id)
        if existing is not None and existing.get("tenant_id") == tenant_id and existing["versions"]:
            raise PoolVersionConflict("pool already exists; append a version instead")
        candidates = list(self._lister(persona_id, tenant_id))
        if len(candidates) != 1:
            raise PoolMemberRefused(
                f"expected exactly one approved paper artifact bound to the persona, found {len(candidates)}"
            )
        entry = candidates[0]
        baseline = str(entry.get("rollback_target") or "").strip()
        if not baseline:
            raise PoolMemberRefused(
                "owner record has no baseline reference", artifact_ref=str(entry.get("registry_id"))
            )
        joined = str(entry.get("approved_at") or entry.get("created_at") or "").strip()
        if not joined:
            raise PoolMemberRefused(
                "owner record has no approval or creation time", artifact_ref=str(entry.get("registry_id"))
            )
        member = PoolMember(
            artifact_ref=str(entry["registry_id"]),
            checksum=str(entry["checksum"]),
            stage="active",
            risk_weight=1.0,
            baseline_ref=baseline,
            joined_at=joined,
            parent_ref=None,
            demand_ref=None,
        )
        return self.append(
            persona_id,
            tenant_id,
            AppendPoolVersionRequest(
                expected_version=0,
                members=[member],
                change_reason="first member derived from the persona's approved paper artifact (owner records)",
            ),
            actor_id=actor_id,
        )


def build_pool_store() -> PersonaStrategyPoolStore:
    from services.foundation.reliable_delivery import build_record_store

    records = build_record_store(
        backend=os.getenv("PERSONA_POOL_STORE_BACKEND", os.getenv("PERSONA_STORE_BACKEND", "json")),
        dsn=os.getenv("PERSONA_POOL_STORE_DSN") or os.getenv("PERSONA_STORE_DSN") or os.getenv("DATABASE_URL"),
        table_name=os.getenv("PERSONA_POOL_STORE_TABLE", "persona.strategy_pools"),
        json_path=os.getenv("PERSONA_POOL_STORE_PATH", "/tmp/pantheon/persona/strategy_pools.json"),
        owner_service="persona-svc",
    )
    return PersonaStrategyPoolStore(records)
